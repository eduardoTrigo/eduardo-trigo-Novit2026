"""Enriquece el contexto local de escalados B1 ya encolados."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from sistema.b1_event_store import AlmacenEventosB1
from sistema.b1_events import MAX_FOTOS_B1
from sistema.part_context import ClientePlanta
from sistema.sensor_detection import es_finito


VENTANA_TENDENCIA = timedelta(hours=3)
MAX_SERIES = 4
MAX_MUESTRAS = 240
_SEPARACION_COBERTURA = timedelta(seconds=120)
_REINTENTAR_TENDENCIA = {"inaccesible", "incompleta", "ausente"}


def _fecha_tendencia(detalle: dict, catalogo: dict) -> datetime:
    ts = datetime.fromisoformat(detalle["ts"])
    if detalle["origen"] == "parte":
        return ts
    offset = detalle["episodio"]["evidencia"][-1].get("offset_minutos")
    if offset is not None:
        return ts.astimezone(timezone(timedelta(minutes=offset)))
    zona = catalogo.get("planta", {}).get("zona_horaria")
    if zona:
        try:
            return ts.astimezone(ZoneInfo(zona))
        except ZoneInfoNotFoundError:
            if zona == "America/Argentina/Buenos_Aires":
                # La física del simulador fija UTC−03:00.
                return ts.astimezone(timezone(timedelta(hours=-3)))
    return ts


def _series(detalle: dict, catalogo: dict) -> dict[str, str]:
    sensores = {
        s["variable"]: s["unidad"] for s in catalogo["sensores"]
        if isinstance(s, dict) and s.get("tag") == detalle["equipo"]
        and isinstance(s.get("variable"), str) and isinstance(s.get("unidad"), str)
    }
    if detalle["origen"] == "sensor":
        variable = detalle["variable"]
        unidad = sensores.get(variable) or detalle["episodio"]["evidencia"][-1].get("unidad_canonica")
        return {variable: unidad} if unidad else {}
    variables = {r.get("variable_canonica") for r in detalle["reglas"] if r["resultado"] == "Escalado"}
    ordenadas = sorted(sensores, key=lambda variable: (variable not in variables, variable))
    return {v: sensores[v] for v in ordenadas[:MAX_SERIES]}


def _resumir_tendencia(datos: list[dict], desde: datetime, hasta: datetime) -> dict:
    if not isinstance(datos, list) or len(datos) > MAX_MUESTRAS:
        return {"estado": "invalida", "motivo": "formato_o_tamano_de_respuesta", "muestras": []}
    por_fecha = {}
    conflictos = set()
    invalidas = 0
    for fila in datos:
        try:
            ts = datetime.fromisoformat(fila["ts"])
            valor = fila["valor"]
            if ts.utcoffset() is None or not desde <= ts <= hasta or (valor is not None and not es_finito(valor)):
                raise ValueError("Muestra de tendencia inválida")
        except (KeyError, TypeError, ValueError):
            invalidas += 1
            continue
        if ts in por_fecha and por_fecha[ts] != valor:
            conflictos.add(ts)
        por_fecha[ts] = valor
    puntos = [(t, v) for t, v in sorted(por_fecha.items()) if t not in conflictos]
    validas = [t for t, v in puntos if v is not None]
    extremos = [desde, *validas, hasta]
    huecos = sum(b - a > _SEPARACION_COBERTURA for a, b in zip(extremos, extremos[1:]))
    completa = bool(validas) and not (huecos or invalidas or conflictos or len(validas) != len(puntos))
    estado = "disponible" if completa else "incompleta" if datos else "ausente"
    return {
        "estado": estado, "huecos": huecos, "invalidas": invalidas + len(conflictos),
        "muestras": [{"ts": t.isoformat(), "valor": v, "calidad": "sin_dato" if v is None else "no_informada"}
                     for t, v in puntos],
    }


class EnriquecedorB1:
    def __init__(self, almacen: AlmacenEventosB1, planta: ClientePlanta):
        self.almacen = almacen
        self.planta = planta

    def _tendencias(self, detalle: dict) -> bool:
        try:
            catalogo = self.planta.catalogo(requerir_sensores=True)
            hasta = _fecha_tendencia(detalle, catalogo)
            desde = hasta - VENTANA_TENDENCIA
            series = _series(detalle, catalogo)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            detalle["estado_tendencias"] = "catalogo_inaccesible"
            return True
        previas = {t["variable"]: t for t in detalle["tendencias"]}
        tendencias = []
        reintentar = False
        for variable, unidad in series.items():
            previa = previas.get(variable)
            if previa is not None and previa["estado"] not in _REINTENTAR_TENDENCIA:
                tendencias.append(previa)
                continue
            try:
                datos = self.planta.historial(detalle["equipo"], variable, hasta, desde=desde)
                tendencia = _resumir_tendencia(datos, desde, hasta)
            except (httpx.HTTPError, ValueError) as error:
                tendencia = {"estado": "inaccesible", "motivo": type(error).__name__, "muestras": []}
            if previa and previa["muestras"] and not tendencia["muestras"]:
                tendencia = {**previa, "ultimo_resultado": tendencia["estado"]}
            reintentar |= tendencia["estado"] in _REINTENTAR_TENDENCIA
            tendencia.update(variable=variable, unidad=unidad, desde=desde.isoformat(), hasta=hasta.isoformat())
            tendencias.append(tendencia)
        detalle["tendencias"] = tendencias
        detalle["estado_tendencias"] = "registradas" if tendencias else "sin_sensores_asociados"
        return reintentar

    def _fotos(self, referencias: list[dict]) -> tuple[list[tuple[str, str, bytes]], bool]:
        archivos = []
        reintentar = False
        aceptadas = 0
        for foto in referencias:
            if foto["estado"] == "invalida":
                continue
            aceptadas += 1
            if foto["estado"] not in {"pendiente", "inaccesible"}:
                continue
            if aceptadas > MAX_FOTOS_B1:
                foto["estado"] = "omitida_limite"
                continue
            try:
                mime, contenido = self.planta.foto_b1(foto["ruta"])
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    foto.update(estado="ausente", motivo="HTTP 404")
                else:
                    foto.update(estado="inaccesible", motivo=f"HTTP {error.response.status_code}")
                    reintentar = True
            except httpx.HTTPError as error:
                foto.update(estado="inaccesible", motivo=type(error).__name__)
                reintentar = True
            except ValueError as error:
                foto.update(estado="invalida", motivo=str(error))
            else:
                foto.update(estado="recuperada", mime=mime, bytes=len(contenido))
                foto.pop("motivo", None)
                archivos.append((foto["ruta"], mime, contenido))
        return archivos, reintentar

    def procesar_siguiente(self) -> bool:
        fila = self.almacen.siguiente_contexto()
        if fila is None:
            return False
        detalle = json.loads(fila["detalle"])
        if detalle["origen"] == "sensor" and "contexto_parte" not in detalle:
            contexto = self.almacen.contexto_sensor(detalle["equipo"], datetime.fromisoformat(detalle["ts"]))
            detalle["contexto_parte"] = contexto
            detalle["estado_parte"] = "relacionado" if contexto else "sin_parte_relacionado"
            if contexto:
                detalle["fotos"] = contexto.pop("fotos")
        detalle["estado_fotos"] = "con_referencias" if detalle["fotos"] else "sin_referencias"
        reintentar_tendencias = self._tendencias(detalle)
        fotos, reintentar_fotos = self._fotos(detalle["fotos"])
        return self.almacen.guardar_contexto(
            fila, detalle, fotos, reintentar=reintentar_tendencias or reintentar_fotos,
        )
