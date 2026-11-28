"""Catálogo, rangos históricos y límites publicados por la planta."""

import math
import time
from datetime import datetime, timedelta

import httpx
from planta_contrato import FilaParte

from sistema.b1_events import ruta_foto_valida
from sistema.part_extraction import convertir_presion
from sistema.plant_tool_models import HistorialSensores


_VENTANA = timedelta(days=45)
_VARIABLE_SENSOR = {"presion_descarga_local": "presion_descarga"}
MAX_BYTES_FOTO_B1 = 512 * 1024
_FIRMAS_FOTO = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
_FIN_FOTO = {"image/jpeg": b"\xff\xd9", "image/png": b"\x00\x00\x00\x00IEND\xaeB\x60\x82"}
_ORIGENES_FOTO = {"http://sim:8100", "http://localhost:8100", "http://127.0.0.1:8100"}


class ClientePlanta:
    def __init__(self, url: str, *, leer_json=None):
        self.url = url.rstrip("/")
        self._catalogo = None
        self._leer_json = leer_json

    def _consultar_json(self, url: str, params: dict | None = None, *, timeout: float = 2.0):
        if self._leer_json is not None:
            return self._leer_json(url, params)
        opciones = {"params": params} if params is not None else {}
        respuesta = httpx.get(url, timeout=timeout, **opciones)
        respuesta.raise_for_status()
        return respuesta.json()

    def catalogo(self, *, requerir_sensores: bool = False, timeout: float | None = None) -> dict:
        cacheado = self._catalogo
        if cacheado is not None and (not requerir_sensores or "sensores" in cacheado):
            return cacheado
        opciones = {} if timeout is None else {"timeout": timeout}
        datos = self._consultar_json(f"{self.url}/api/planta", **opciones)
        if not isinstance(datos, dict) or not isinstance(datos.get("equipos"), list):
            raise ValueError("Catálogo de equipos inválido")
        if not datos["equipos"]:
            raise ValueError("Catálogo de equipos vacío")
        if (requerir_sensores or "sensores" in datos) and not isinstance(datos.get("sensores"), list):
            raise ValueError("Catálogo de sensores inválido")
        self._catalogo = datos
        return datos

    def estado_operativo(self) -> dict:
        respuesta = httpx.get(f"{self.url}/api/estado", timeout=2.0)
        respuesta.raise_for_status()
        datos = respuesta.json()
        if not isinstance(datos, dict):
            raise ValueError("Estado operativo inválido")
        return datos

    def foto_b1(self, ruta: str) -> tuple[str, bytes]:
        return self._descargar_foto(ruta, plazo=3)

    def foto_parte(self, ruta: str, *, plazo: float) -> tuple[str, bytes]:
        if self.url not in _ORIGENES_FOTO or not math.isfinite(plazo) or plazo <= 0:
            raise ValueError("Origen o plazo de foto no autorizado")
        return self._descargar_foto(ruta, plazo=plazo, completa=True)

    def _descargar_foto(self, ruta: str, *, plazo: float, completa=False) -> tuple[str, bytes]:
        if not ruta_foto_valida(ruta):
            raise ValueError("Ruta de foto B1 no autorizada")
        vencimiento = time.monotonic() + plazo
        opciones = {"trust_env": False} if completa else {}
        with httpx.stream("GET", self.url + ruta, timeout=min(2.0, plazo), follow_redirects=False,
                          headers={"Accept-Encoding": "identity"}, **opciones) as respuesta:
            respuesta.raise_for_status()
            mime = respuesta.headers.get("content-type", "").split(";")[0].strip().lower()
            firma = _FIRMAS_FOTO.get(mime)
            if firma is None or respuesta.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("Formato de foto B1 no admitido")
            declarada = respuesta.headers.get("content-length")
            longitud = int(declarada) if declarada is not None else None
            if longitud is not None and not 0 <= longitud <= MAX_BYTES_FOTO_B1:
                raise ValueError("Tamaño declarado de foto B1 inválido")
            contenido = bytearray()
            for bloque in respuesta.iter_bytes(chunk_size=16 * 1024):
                if time.monotonic() > vencimiento:
                    raise httpx.ReadTimeout("Plazo de descarga de foto B1 agotado")
                if len(contenido) + len(bloque) > MAX_BYTES_FOTO_B1:
                    raise ValueError("Foto B1 supera el límite de tamaño")
                contenido.extend(bloque)
            if not contenido.startswith(firma):
                raise ValueError("Firma de foto B1 inválida")
            if completa:
                if time.monotonic() > vencimiento:
                    raise httpx.ReadTimeout("Plazo de descarga de foto agotado")
                if (respuesta.status_code != 200 or "content-range" in respuesta.headers
                        or (longitud is not None and len(contenido) != longitud)
                        or not contenido.endswith(_FIN_FOTO[mime])):
                    raise ValueError("Foto incompleta")
            return mime, bytes(contenido)

    def historial(
        self, tag: str, variable: str, hasta: datetime, *, desde: datetime | None = None, timeout: float | None = None,
    ) -> list[dict]:
        if hasta.utcoffset() is None or (desde is not None and desde.utcoffset() is None):
            raise ValueError("El historian requiere fechas con zona horaria")
        inicio = hasta - _VENTANA if desde is None else desde
        if inicio > hasta:
            raise ValueError("Intervalo de historian invertido")
        parametros = {"desde": inicio.isoformat(), "hasta": hasta.isoformat()}
        url = f"{self.url}/api/historian/{tag}/{variable}"
        intentos = 2 if desde is None else 1
        for intento in range(intentos):
            try:
                opciones = {} if timeout is None else {"timeout": timeout}
                muestras = self._consultar_json(url, parametros, **opciones)
                if not isinstance(muestras, list):
                    raise ValueError("Respuesta del historian inválida")
                return muestras
            except (httpx.TimeoutException, httpx.NetworkError):
                if intento == intentos - 1:
                    raise

    def historial_ordenes(
        self, *, equipo: str | None = None, desde: datetime | None = None, hasta: datetime | None = None,
    ) -> list[dict]:
        parametros = {}
        if equipo is not None:
            parametros["equipo"] = equipo
        if desde is not None:
            parametros["desde"] = desde.isoformat()
        if hasta is not None:
            parametros["hasta"] = hasta.isoformat()
        ordenes = self._consultar_json(f"{self.url}/api/historial-ordenes", parametros)
        if not isinstance(ordenes, list):
            raise ValueError("Respuesta del historial de órdenes inválida")
        return ordenes


def _finito(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _rango(valor, minimo, maximo):
    fuera = (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo)
    return {"estado": "fuera_de_rango" if fuera else "compatible", "minimo": minimo, "maximo": maximo}


def _historico(muestras, valor, hasta):
    validas = {}
    for muestra in muestras:
        if not isinstance(muestra, dict) or not _finito(muestra.get("valor")):
            continue
        try:
            fecha = datetime.fromisoformat(muestra["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if fecha.utcoffset() is not None and hasta - _VENTANA <= fecha <= hasta:
            validas[fecha] = muestra["valor"]
    cobertura = {"muestras": len(validas), "recibidas": len(muestras),
                 "primera_muestra": min(validas).isoformat() if validas else None,
                 "ultima_muestra": max(validas).isoformat() if validas else None}
    if len(validas) < 2:
        return {"estado": "insuficiente" if muestras else "vacio"} | cobertura
    return _rango(valor, min(validas.values()), max(validas.values())) | cobertura


def verificar_lectura(fila: FilaParte, ts: datetime, catalogo: dict, historial) -> dict:
    variable = _VARIABLE_SENSOR.get(fila.variable, fila.variable)
    sensor = next((s for s in catalogo.get("sensores", [])
                   if (s["tag"], s["variable"]) == (fila.tag, variable)), None)
    evidencia = {"fila": fila.model_dump(), "variable_sensor": variable,
                 "historico": {"estado": "sin_sensor"}, "limites": {"estado": "sin_fuente"}}
    if sensor is None:
        return evidencia

    unidad = sensor["unidad"]
    valor = fila.valor if fila.unidad == unidad else convertir_presion(fila.valor, fila.unidad, unidad)
    if valor is None and fila.unidad != unidad:
        evidencia["historico"] = {"estado": "unidad_incompatible"}
        return evidencia

    evidencia.update(comparado=valor, unidad=unidad)
    baja, alta = sensor.get("alarma_baja"), sensor.get("alarma_alta")
    baja = baja if _finito(baja) else None
    alta = alta if _finito(alta) else None
    if (baja is not None or alta is not None) and not (baja is not None and alta is not None and baja > alta):
        evidencia["limites"] = _rango(valor, baja, alta)

    try:
        if ts.utcoffset() is None:
            raise ValueError("Fecha de nota sin zona horaria")
        inicio = ts - _VENTANA
        muestras = historial(fila.tag, variable, ts)
        cobertura = {"fuente": "historian", "desde": inicio.isoformat(), "hasta": ts.isoformat()}
        if isinstance(muestras, HistorialSensores):
            if (muestras.tag, muestras.variable, muestras.unidad) != (fila.tag, variable, unidad):
                raise ValueError("Serie histórica de otra medición")
            serie = {"truncado": muestras.truncado, "total": muestras.total,
                     "desde_fuente": muestras.desde.isoformat(), "hasta_fuente": muestras.hasta.isoformat()}
            datos = _historico([m.model_dump(mode="json") for m in muestras.items], valor, ts)
            if (muestras.truncado or muestras.total != len(muestras.items)
                    or muestras.desde > inicio or muestras.hasta < ts):
                for campo in ("minimo", "maximo"):
                    datos.pop(campo, None)
                datos["estado"] = "incompleto"
            evidencia["historico"] = datos | cobertura | serie
        else:
            evidencia["historico"] = _historico(muestras, valor, ts) | cobertura
    except (httpx.HTTPError, ValueError):
        evidencia["historico"] = {"estado": "inaccesible"}
    return evidencia
