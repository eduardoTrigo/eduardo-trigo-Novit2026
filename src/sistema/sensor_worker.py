"""Consume sensores y energía fuera del hilo MQTT."""

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from queue import Empty, Full, Queue

import httpx
from planta_contrato import Calidad, EstadoEnergia, LecturaSensor, Topicos
from pydantic import ValidationError

from sistema.mqtt_client import PREFIJO_SENSORES
from sistema.part_context import ClientePlanta
from sistema.sensor_detection import LimitesSensor, avanzar, es_finito, evaluar, intervalo_historial
from sistema.sensor_store import AlmacenSensoresB1

logger = logging.getLogger(__name__)
MAX_BYTES_MENSAJE = 4096
MAX_FILAS_HISTORIAN = 32
TOLERANCIA_ESTADO = timedelta(seconds=10)
PAUSA_CATALOGO = 2.0
PAUSA_ESTADO = 0.5
PAUSA_HISTORIAL = 1.0
PAUSA_ERROR_SENSOR = 0.5


def _clave(topico: str) -> tuple[str, str] | None:
    partes = topico.removeprefix(PREFIJO_SENSORES).split("/")
    if topico.startswith(PREFIJO_SENSORES) and len(partes) == 2 and all(partes):
        return partes[0], partes[1]
    return None


def _indexar_limites(catalogo: dict) -> dict[tuple[str, str], LimitesSensor | None]:
    tipos = {e["tag"]: e.get("tipo") for e in catalogo["equipos"]
             if isinstance(e, dict) and isinstance(e.get("tag"), str)}
    limites = {}
    for sensor in catalogo["sensores"]:
        if not isinstance(sensor, dict):
            continue
        tag, variable = sensor.get("tag"), sensor.get("variable")
        if not isinstance(tag, str) or tag not in tipos or not isinstance(variable, str):
            continue
        clave = (tag, variable)
        unidad = sensor.get("unidad")
        baja, alta, disparo = (sensor.get(c) for c in ("alarma_baja", "alarma_alta", "disparo"))
        if (clave in limites or not isinstance(unidad, str) or not unidad.strip()
                or any(v is not None and not es_finito(v) for v in (baja, alta, disparo))
                or (baja is None and alta is None)
                or (baja is not None and alta is not None and baja >= alta)):
            limites[clave] = None
        else:
            limites[clave] = LimitesSensor(unidad, baja, alta, disparo, tipos[tag])
    return limites


def _estado_para(instantanea: dict, tag: str, ts: datetime) -> str | None:
    try:
        fecha = datetime.fromisoformat(instantanea["ts"])
    except (KeyError, TypeError, ValueError):
        return None
    if fecha.utcoffset() is None or not timedelta(0) <= fecha - ts < TOLERANCIA_ESTADO:
        return None
    if instantanea.get("hay_energia") is False:
        return "sin_energia"
    for bomba in _filas_estado(instantanea.get("bombas")):
        if bomba.get("tag") == tag and bomba.get("estado") in ("marcha", "parada", "mantenimiento", "disparo"):
            return bomba["estado"]
    for filtro in _filas_estado(instantanea.get("filtros")):
        if filtro.get("tag") == tag and type(filtro.get("en_cambio")) is bool:
            return "mantenimiento" if filtro["en_cambio"] else "marcha"
    return None


def _filas_estado(datos):
    if isinstance(datos, list):
        yield from (fila for fila in datos if isinstance(fila, dict))


class WorkerSensoresB1:
    def __init__(
        self, almacen: AlmacenSensoresB1, planta: ClientePlanta, *,
        capacidad: int = 256, reloj: Callable[[], float] = time.monotonic,
    ):
        if capacidad <= 0:
            raise ValueError("La cola de sensores necesita una capacidad positiva")
        self.almacen = almacen
        self.planta = planta
        self.reloj = reloj
        self.cola: Queue[tuple[int, str, bytes]] = Queue(capacidad)
        self.perdidas = 0
        self._generacion = 0
        self._generacion_aplicada: int | None = None
        self._energia: EstadoEnergia | None = None
        self._limites: dict[tuple[str, str], LimitesSensor | None] | None = None
        self._proximo_catalogo = float("-inf")
        self._instantanea: dict = {}
        self._proximo_estado = float("-inf")
        self._proximo_historial: dict[tuple[str, str], float] = {}
        self._pausa_historial_hasta = float("-inf")
        self.detencion = threading.Event()
        self.hilo: threading.Thread | None = None

    def marcar_discontinuidad(self) -> None:
        # El hilo MQTT es el único productor de mensajes y avisos de desconexión.
        self._generacion += 1

    def encolar(self, topico: str, payload: bytes) -> bool:
        if len(payload) > MAX_BYTES_MENSAJE or (topico != Topicos.ENERGIA and _clave(topico) is None):
            self.perdidas += 1
            self.marcar_discontinuidad()
            return False
        try:
            self.cola.put_nowait((self._generacion, topico, payload))
        except Full:
            self.perdidas += 1
            self.marcar_discontinuidad()
            return False
        return True

    def _sincronizar_generacion(self) -> None:
        generacion = self._generacion
        if self._generacion_aplicada == generacion:
            return
        motivo = "reinicio" if generacion == 0 else "discontinuidad_mqtt"
        self.almacen.interrumpir(motivo)
        self._energia = None
        self._instantanea = {}
        self._generacion_aplicada = generacion
        if generacion:
            logger.warning("Discontinuidad de sensores MQTT. Mensajes descartados en ingreso: %s", self.perdidas)

    def _actualizar_catalogo(self) -> None:
        if self._limites is None and self.reloj() >= self._proximo_catalogo:
            try:
                self._limites = _indexar_limites(self.planta.catalogo(requerir_sensores=True))
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                self._proximo_catalogo = self.reloj() + PAUSA_CATALOGO
                logger.warning("Catálogo B1 de sensores inaccesible", exc_info=True)

    def _estado(self, muestra: LecturaSensor, consultar: bool) -> str | None:
        if self._energia and self._energia.ts <= muestra.ts and not self._energia.hay_energia:
            return "sin_energia"
        estado = _estado_para(self._instantanea, muestra.tag, muestra.ts)
        if estado is not None or not consultar or self.reloj() < self._proximo_estado:
            return estado
        try:
            self._instantanea = self.planta.estado_operativo()
        except (httpx.HTTPError, ValueError):
            logger.debug("Estado operativo de sensores inaccesible", exc_info=True)
        finally:
            self._proximo_estado = self.reloj() + PAUSA_ESTADO
        return _estado_para(self._instantanea, muestra.tag, muestra.ts)

    def _recibir_energia(self, payload: bytes) -> None:
        try:
            estado = EstadoEnergia.model_validate_json(payload, strict=True)
            if estado.ts.utcoffset() is None:
                raise ValueError("Timestamp de energía sin zona")
        except (ValidationError, ValueError):
            self.almacen.interrumpir("energia_invalida")
            self._instantanea = {}
            return
        if self._energia and estado.ts < self._energia.ts:
            return
        if self._energia is None or self._energia.hay_energia != estado.hay_energia:
            self.almacen.interrumpir("cambio_energia")
            self._instantanea = {}
        self._energia = estado

    def _historial(self, muestra, limites, serie, intervalo):
        clave = (muestra.tag, muestra.variable)
        ahora = self.reloj()
        if ahora < max(self._pausa_historial_hasta, self._proximo_historial.get(clave, float("-inf"))):
            return ()
        desde, hasta = (ts.astimezone(muestra.ts.tzinfo) for ts in intervalo)
        try:
            filas = self.planta.historial(*clave, hasta, desde=desde)
            if len(filas) > MAX_FILAS_HISTORIAN:
                return ()
            puntos = []
            for fila in filas:
                valor = fila["valor"]
                if valor is not None and not es_finito(valor):
                    return ()
                anterior = LecturaSensor(
                    tag=muestra.tag, variable=muestra.variable, ts=datetime.fromisoformat(fila["ts"]),
                    unidad=limites.unidad, valor=valor,
                    calidad=Calidad.sin_dato if valor is None else Calidad.buena,
                )
                puntos.append(evaluar(anterior, limites, None, en_recuperacion=serie.activo is not None,
                                      origen="historian"))
            return tuple(puntos)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            self._pausa_historial_hasta = self.reloj() + PAUSA_CATALOGO
            logger.debug("Historian B1 inaccesible o inválido", exc_info=True)
            return ()
        finally:
            self._proximo_historial[clave] = self.reloj() + PAUSA_HISTORIAL

    def _procesar_muestra(self, generacion: int, clave: tuple[str, str], payload: bytes) -> None:
        try:
            muestra = LecturaSensor.model_validate_json(payload, strict=True)
            if (muestra.tag, muestra.variable) != clave or muestra.ts.utcoffset() is None:
                raise ValueError("Tópico, payload o fecha incoherentes")
        except (ValidationError, ValueError):
            self.almacen.interrumpir("muestra_invalida", clave)
            return
        self._actualizar_catalogo()
        if self._limites is None or clave not in self._limites:
            self.almacen.interrumpir("catalogo_inaccesible" if self._limites is None else "sensor_sin_catalogo", clave)
            return
        limites = self._limites[clave]
        huella = limites.huella if limites else "sin_limites"
        try:
            anterior = self.almacen.leer(clave)
        except sqlite3.OperationalError as error:
            codigo = getattr(error, "sqlite_errorcode", None)
            if codigo is None or (codigo & 0xFF) != sqlite3.SQLITE_BUSY:
                raise
            logger.warning("Lectura B1 bloqueada en SQLite para %s. Se reintentará una vez", clave)
            if self.detencion.wait(PAUSA_ERROR_SENSOR) or generacion != self._generacion:
                return
            anterior = self.almacen.leer(clave)
        serie = anterior.serie
        recuperacion = serie.activo is not None
        provisional = evaluar(muestra, limites, None, en_recuperacion=recuperacion)
        requiere_estado = provisional.motivo == "estado_operativo_no_corroborado"
        nueva = serie.ultimo is None or muestra.ts > serie.ultimo.ts
        estado = self._estado(muestra, consultar=requiere_estado and nueva)
        punto = provisional if estado is None else evaluar(muestra, limites, estado, en_recuperacion=recuperacion)
        intervalo = intervalo_historial(serie, punto, huella)
        historial = self._historial(muestra, limites, serie, intervalo) if intervalo and not requiere_estado else ()
        cambio = avanzar(serie, punto, epoca=self.almacen.epoca, clave=clave, huella=huella, historial=historial)
        if generacion == self._generacion:
            self.almacen.guardar(clave, anterior, cambio)

    def procesar_siguiente(self) -> bool:
        try:
            generacion, topico, payload = self.cola.get_nowait()
        except Empty:
            self._sincronizar_generacion()
            return False
        self._sincronizar_generacion()
        if generacion == self._generacion_aplicada:
            if topico == Topicos.ENERGIA:
                self._recibir_energia(payload)
            else:
                self._procesar_muestra(generacion, _clave(topico), payload)
        return True

    def iniciar(self) -> None:
        if self.hilo is not None:
            raise RuntimeError("Worker de sensores B1 ya iniciado")
        self.hilo = threading.Thread(target=self._ejecutar, name="pia3-sensores-b1", daemon=True)
        self.hilo.start()

    def _ejecutar(self) -> None:
        while not self.detencion.is_set():
            try:
                if not self.procesar_siguiente():
                    self.detencion.wait(0.1)
            except Exception:
                logger.exception("Error procesando sensores B1")
                # La muestra que falló ya salió de la cola y rompe la continuidad.
                self._generacion_aplicada = None
                self.detencion.wait(PAUSA_ERROR_SENSOR)

    def detener(self) -> bool:
        self.detencion.set()
        if self.hilo is None:
            return True
        self.hilo.join(timeout=5)
        return not self.hilo.is_alive()
