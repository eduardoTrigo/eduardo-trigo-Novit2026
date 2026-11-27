"""Cadena del parte. Produce planes de salida y carece de acceso a persistencia."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Protocol

import httpx
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context
from planta_contrato import FilaParte, MensajeTablet, NotaRecorrida, RespuestaOperador
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from sistema.context_builder import ErrorContexto, construir_contexto
from sistema.context_builder_models import (
    Alcance, DatosContexto, EntradaContexto, FalloFuente, Lectura, NotaContexto, Presupuesto,
)
from sistema.execution_budget import AisladorLecturas, Conteo, ControlEjecucion, CorteEjecucion
from sistema.part_clarification import preguntar, resolver
from sistema.part_context import _VARIABLE_SENSOR, _VENTANA, verificar_lectura
from sistema.part_extraction import extraer_parte
from sistema.part_messages import crear_borrador
from sistema.part_photos import capturar_fotos
from sistema.part_reply import interpretar_respuesta
from sistema.part_store import ParteGuardado
from sistema.plant_tool_models import Equipo, HistorialSensores, Muestra, Pagina, Sensor
from sistema.plant_tools import paginar_resultado


_FILAS = TypeAdapter(list[FilaParte])
_ESPERA = {"borrador_pendiente", "aclaracion_pendiente"}
_TEXTO_MAX = 32000


class ModeloNoDisponible(Exception):
    """Fallo transitorio de un adaptador de extracción."""


class SalidaModelo(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    filas: list[FilaParte]
    tokens_entrada: Conteo | None = None
    tokens_salida: Conteo | None = None


class ProveedorExtraccion(Protocol):
    nombre: str
    revision: str

    def extraer(self, contexto_json: str, *, timeout: float, max_tokens: int) -> dict: ...


@dataclass
class PlanParte:
    estado: str | None
    filas: list[FilaParte] | None = None
    mensaje: MensajeTablet | None = None
    contexto: dict | None = None
    resultado: str | None = None
    archivos_fotos: dict[str, tuple[str, bytes]] = field(default_factory=dict)


@dataclass
class _Turno:
    control: ControlEjecucion
    nota: NotaRecorrida
    texto: str
    parte: ParteGuardado | None = None
    respuesta: RespuestaOperador | None = None
    fase: str | None = None
    catalogo: dict | None = None
    historiales: dict = field(default_factory=dict)
    plan: PlanParte | None = None
    contexto: object = None


def _validar_filas(filas):
    valores = _FILAS.validate_python([f.model_dump() if isinstance(f, FilaParte) else f for f in filas], strict=True)
    if (any(f.valor is not None and not isfinite(f.valor) for f in valores)
            or len({(f.tag, f.variable) for f in valores}) != len(valores)):
        raise CorteEjecucion("salida_invalida")
    return valores


class AgentePartes:
    def __init__(self, catalogo, historial=None, *, fuentes_con_timeout=False, modelo: ProveedorExtraccion | None = None,
                 foto=None):
        self._catalogo = catalogo
        self._historial = historial
        self._fuentes_con_timeout = fuentes_con_timeout
        self._modelo = modelo
        self._foto = foto
        self._aislador = AisladorLecturas()
        self._aislador_fotos = AisladorLecturas()
        ensamblar = RunnableLambda(self._ensamblar, name="contexto_parte")
        salida = RunnableLambda(self._validar_salida, name="validacion_salida")
        self.cadena_nota = RunnableLambda(self._derivar, name="derivar_nota") | ensamblar | salida
        self.cadena_respuesta = RunnableLambda(self._interpretar, name="interpretar_operador") | ensamblar | salida

    def nota(self, control, nota, texto):
        with tracing_context(enabled=False):
            return self.cadena_nota.invoke(_Turno(control, nota, texto))

    def respuesta(self, control, parte, respuesta, fase):
        with tracing_context(enabled=False):
            return self.cadena_respuesta.invoke(_Turno(control, parte.nota, respuesta.texto, parte, respuesta, fase))

    def _fuente(self, turno, herramienta, operacion, argumentos=None):
        control = turno.control
        while True:
            try:
                return control.paso("lectura", operacion, aislador=self._aislador,
                                    herramienta=herramienta, argumentos=argumentos)
            except (httpx.TimeoutException, httpx.NetworkError):
                if not control.reintentar():
                    raise

    def _cargar_catalogo(self, turno):
        if turno.catalogo is None:
            try:
                def consultar():
                    if self._fuentes_con_timeout:
                        return self._catalogo(timeout=min(2.0, turno.control.plazo_lectura()))
                    return self._catalogo()

                turno.catalogo = self._fuente(turno, "consultar_equipos", consultar)
            except (httpx.HTTPError, ValueError):
                raise CorteEjecucion("fuente_no_disponible") from None
        return turno.catalogo

    def _leer_historial(self, turno, tag, variable, hasta):
        clave = tag, variable, hasta
        if clave not in turno.historiales:
            def consultar():
                if self._fuentes_con_timeout:
                    return self._historial(tag, variable, hasta, desde=hasta - _VENTANA,
                                           timeout=min(2.0, turno.control.plazo_lectura()))
                return self._historial(tag, variable, hasta)

            try:
                turno.historiales[clave] = self._fuente(
                    turno, "consultar_historial_sensores", consultar, {"tag": tag, "variable": variable},
                )
            except (httpx.HTTPError, ValueError):
                turno.historiales[clave] = None
                raise
        datos = turno.historiales[clave]
        if datos is None:
            raise ValueError("Lectura anterior inaccesible")
        return datos

    def _derivar(self, turno):
        if not turno.texto:
            turno.plan = PlanParte("revision_manual", [])
            return turno
        if len(turno.texto) > _TEXTO_MAX:
            raise CorteEjecucion("contexto_insuficiente")
        catalogo = self._cargar_catalogo(turno)
        extraido = turno.control.paso("extraccion_determinista", lambda: extraer_parte(turno.texto, catalogo),
                                      aislador=self._aislador)
        filas = turno.control.paso("validar_candidatos", lambda: _validar_filas(extraido.filas))
        contexto = {"pendientes": extraido.dudas, "no_interpretada": extraido.no_interpretada,
                    "segmentos_no_interpretados": extraido.segmentos_no_interpretados}
        if turno.nota.id.startswith("VIVO-"):
            turno.plan = PlanParte("revision_manual", filas, contexto=contexto)
            return turno
        turno.plan = self._preparar(turno, filas, contexto, catalogo, 1)
        return turno

    def _preparar(self, turno, filas, contexto, catalogo, version, resultado=None):
        def preparar():
            pendientes = contexto.setdefault("pendientes", [])
            if not pendientes and not contexto.get("no_interpretada") and self._historial is not None:
                evidencia = contexto.setdefault("evidencia", [])
                confirmadas = [r["duda"]["fila"] for r in contexto.get("resoluciones", [])
                               if r["resultado"] == "resuelta" and r.get("duda", {}).get("tipo") == "rango"]
                for fila in filas:
                    if fila.valor is None:
                        continue
                    datos = fila.model_dump()
                    comprobacion = next((e for e in evidencia if e["fila"] == datos), None)
                    if comprobacion is None:
                        comprobacion = turno.control.paso("verificar_lectura", lambda: verificar_lectura(
                            fila, turno.nota.ts, catalogo, lambda *args: self._leer_historial(turno, *args),
                        ))
                        evidencia.append(comprobacion)
                    fuera = any(comprobacion[f]["estado"] == "fuera_de_rango" for f in ("historico", "limites"))
                    if fuera and datos not in confirmadas:
                        pendientes.append({"tipo": "rango", "fila": datos})
            mensaje = None
            if pendientes:
                equipos = {e["tag"]: e for e in catalogo["equipos"]}
                duda = pendientes[0]
                if duda["tipo"] == "valor" and duda["unidad"] is not None:
                    duda["confirmar_candidato"] = True
                estado = "aclaracion_pendiente"
                mensaje = preguntar(turno.nota, duda, equipos, filas, version)
            elif contexto.get("no_interpretada"):
                estado = "revision_manual"
            elif not filas:
                estado = "sin_contenido"
            else:
                estado = "borrador_pendiente"
                mensaje = crear_borrador(turno.nota, filas, version=version)
            return PlanParte(estado, filas, mensaje, contexto, resultado)

        return turno.control.paso("preparar_turno", preparar)

    def _interpretar(self, turno):
        parte, respuesta = turno.parte, turno.respuesta
        if turno.fase == "stt_agotado":
            turno.plan = PlanParte("revision_manual", resultado="stt_fallido")
            return turno
        if len(turno.texto) > _TEXTO_MAX:
            raise CorteEjecucion("contexto_insuficiente")
        aclaracion = parte.estado == "aclaracion_pendiente"
        resultado, filas = turno.control.paso("interpretar_respuesta", lambda: interpretar_respuesta(
            turno.texto, [] if aclaracion else parte.filas, lambda: self._cargar_catalogo(turno),
        ))
        duda = None
        if aclaracion and resultado != "rechazada":
            duda = parte.contexto["pendientes"][0]
            catalogo = self._cargar_catalogo(turno)
            resultado, filas, siguiente = turno.control.paso("resolver_aclaracion", lambda: resolver(
                duda, turno.texto, catalogo, parte.filas,
            ), aislador=self._aislador)
            if resultado == "insuficiente":
                filas = None
        if filas is None:
            if resultado == "insuficiente" and respuesta.audio_url and turno.fase is None:
                resultado = "requiere_stt"
            estado = {"confirmada": "confirmado", "rechazada": "rechazado"}.get(resultado)
            turno.plan = PlanParte(estado, resultado=resultado)
            return turno
        filas = turno.control.paso("validar_candidatos", lambda: _validar_filas(filas))
        contexto = parte.contexto
        resolucion = {
            "respuesta_id": respuesta.id, "mensaje_id": respuesta.mensaje_id, "resultado": resultado,
            "filas": [f.model_dump() for f in filas],
        }
        if duda is not None:
            resolucion["duda"] = duda
        contexto.setdefault("resoluciones", []).append(resolucion)
        if resultado == "conflicto_lecturas":
            turno.plan = PlanParte("revision_manual", contexto=contexto, resultado=resultado)
            return turno
        if duda is not None:
            pendientes = contexto["pendientes"]
            pendientes.pop(0)
            if siguiente is not None:
                pendientes.insert(0, siguiente)
            if duda["tipo"] == "rango":
                claves = [f.model_dump() for f in filas]
                pendientes[:] = [p for p in pendientes if p["tipo"] != "rango" or p["fila"] in claves]
        catalogo = self._cargar_catalogo(turno) if aclaracion or self._historial is not None else {}
        turno.plan = self._preparar(turno, filas, contexto, catalogo, parte.version + 1, resultado)
        return turno

    def _ensamblar(self, turno):
        plan = turno.plan
        if not turno.texto.strip() or plan.estado not in _ESPERA:
            return turno
        try:
            turno.contexto = turno.control.paso("construir_contexto", lambda: self._contexto(turno),
                                               aislador=self._aislador)
        except ErrorContexto as error:
            motivo = "contexto_insuficiente" if error.codigo == "presupuesto_insuficiente" else "salida_invalida"
            raise CorteEjecucion(motivo) from None
        turno.control.consumo.tokens_contexto += turno.contexto.uso.tokens
        return turno

    def _contexto(self, turno):
        ahora = max(datetime.now(timezone.utc), turno.nota.ts)
        filas = turno.plan.filas or []
        tags = {f.tag for f in filas}
        for duda in (turno.plan.contexto or {}).get("pendientes", []):
            if duda["tipo"] == "equipo":
                tags.update(duda.get("opciones", []))
            if "fila" in duda:
                tags.add(duda["fila"]["tag"])
            if isinstance(duda.get("tag"), str):
                tags.add(duda["tag"])
        variables = {_VARIABLE_SENSOR.get(f.variable, f.variable) for f in filas}
        equipos, series, fallos = _proyectar_fuentes(turno, tags, ahora)
        cfg = turno.control.presupuesto
        return construir_contexto(EntradaContexto(
            nota=NotaContexto.desde_recorrida(turno.nota, turno.texto),
            tarea="parte" if turno.parte is None else "aclaracion", evaluado_en=ahora,
            alcance=Alcance(equipos=tuple(sorted(tags)), variables=tuple(sorted(variables))),
            datos=DatosContexto(equipos=equipos, historian=series, fallos=fallos),
            presupuesto=Presupuesto(entrada=cfg.tokens_contexto, reserva_salida=cfg.reserva_salida),
        ))

    def _validar_salida(self, turno):
        if turno.plan.mensaje is not None:
            if self._modelo is not None and turno.parte is None:
                self._proponer(turno)
            turno.control.paso("validar_tablet", lambda: MensajeTablet.model_validate_json(
                turno.plan.mensaje.model_dump_json(), strict=True,
            ))
        if turno.parte is None and turno.nota.fotos:
            fotos, archivos = capturar_fotos(
                turno.nota, turno.texto, turno.catalogo or {}, turno.plan.filas or [],
                self._foto, turno.control, self._aislador_fotos,
            )
            turno.plan.contexto = (turno.plan.contexto or {}) | {"fotos": fotos}
            turno.plan.archivos_fotos = archivos
        return turno.plan

    def _proponer(self, turno):
        control, modelo = turno.control, self._modelo
        estimados = turno.contexto.uso.tokens
        control.modo = "adaptador"
        control.modelo, control.revision_modelo = modelo.nombre, modelo.revision
        while True:
            try:
                def solicitar():
                    return modelo.extraer(turno.contexto.serializar_contexto(), timeout=control.plazo_lectura(),
                                          max_tokens=control.presupuesto.reserva_salida)

                respuesta = control.paso("proponer_extraccion", solicitar, aislador=self._aislador,
                                         antes=lambda: control.reservar_modelo(estimados))
                salida = control.paso("validar_modelo", lambda: SalidaModelo.model_validate(respuesta, strict=True))
                if salida.tokens_entrada is not None and salida.tokens_salida is not None:
                    control.ajustar_modelo(estimados, salida.tokens_entrada, salida.tokens_salida)
                if _validar_filas(salida.filas) != turno.plan.filas:
                    raise CorteEjecucion("salida_invalida")
                return
            except (ModeloNoDisponible, TimeoutError):
                if control.reintentar():
                    continue
                motivo = "reintentos_agotados" if control.consumo.reintentos else "modelo_no_disponible"
            except ValidationError:
                motivo = "salida_invalida"
            except CorteEjecucion as error:
                if error.motivo in {"timeout_global", "cancelada", "pasos_agotados"}:
                    raise
                motivo = error.motivo
            control.fallback = motivo
            control.modo = "determinista"
            control.paso("fallback_determinista", lambda: _validar_filas(turno.plan.filas))
            return


def _proyectar_fuentes(turno, tags, ahora):
    catalogo = turno.catalogo or {}
    originales = catalogo.get("sensores", [])
    sensores = [Sensor.model_validate(s) for s in originales if s["tag"] in tags]
    equipos = []
    for equipo in catalogo.get("equipos", []):
        if equipo["tag"] in tags:
            campos = {k: v for k, v in equipo.items() if k in Equipo.model_fields}
            campos.setdefault("descripcion", "")
            equipos.append(Equipo(**(campos | {"sensores": [s for s in sensores if s.tag == equipo["tag"]]})))
    datos_equipos = Lectura[Pagina[Equipo]](
        obtenida_en=ahora, dato=Pagina[Equipo](items=equipos, total=len(equipos), truncado=False),
    ) if turno.catalogo is not None else None
    series, fallos = [], []
    for (tag, variable, hasta), lecturas in turno.historiales.items():
        if lecturas is None:
            fallos.append(FalloFuente(fuente="consultar_historial_sensores", estado="no_disponible"))
            continue
        if isinstance(lecturas, HistorialSensores):
            series.append(Lectura[HistorialSensores](obtenida_en=ahora, dato=lecturas))
            continue
        unidad = next(s.unidad for s in sensores if (s.tag, s.variable) == (tag, variable))
        muestras = []
        for lectura in lecturas:
            try:
                muestra = Muestra.model_validate_json(json.dumps(lectura))
            except ValidationError:
                continue
            if hasta - _VENTANA <= muestra.ts <= hasta:
                muestras.append(muestra)
        muestras.sort(key=lambda m: m.ts)
        pagina = HistorialSensores(tag=tag, variable=variable, unidad=unidad, desde=hasta - _VENTANA,
                                   hasta=hasta, **paginar_resultado(muestras, 60))
        series.append(Lectura[HistorialSensores](obtenida_en=ahora, dato=pagina))
    return datos_equipos, tuple(series), tuple(fallos)
