"""Reglas puras B1 sobre timestamps simulados."""

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from planta_contrato import Calidad, LecturaSensor

from sistema.part_extraction import convertir_presion, normalizar_unidad


MIN_MUESTRAS = 3
DURACION = timedelta(seconds=120)
HUECO_MAXIMO = timedelta(seconds=120)
HISTORIAL_MAXIMO = timedelta(minutes=10)
MAX_PUNTOS = 64
_PARADAS = {"parada", "reserva", "mantenimiento"}
_PRESIONES = {"presion_succion", "presion_descarga"}


def _huella(datos) -> str:
    return sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class LimitesSensor:
    unidad: str
    baja: float | None = None
    alta: float | None = None
    disparo: float | None = None
    tipo_equipo: str | None = None

    @property
    def huella(self) -> str:
        return _huella(asdict(self))


@dataclass(frozen=True)
class Punto:
    ts: datetime
    valor: float | str | None
    unidad: str
    calidad: str
    origen: str
    estado: str | None
    nivel: str = "indeterminado"
    motivo: str = "sin_limites"
    valor_canonico: float | None = None
    unidad_canonica: str | None = None
    alarma_baja: float | None = None
    alarma_alta: float | None = None
    disparo: float | None = None
    offset_minutos: int | None = None


@dataclass(frozen=True)
class Episodio:
    id: str
    direccion: str
    inicio: datetime
    apertura: datetime
    evidencia: tuple[Punto, ...]
    minimo: float
    maximo: float


@dataclass(frozen=True)
class Serie:
    ultimo: Punto | None = None
    ventana: tuple[Punto, ...] = ()
    activo: Episodio | None = None
    huella: str | None = None
    motivo: str | None = None
    interrumpida: bool = False


@dataclass(frozen=True)
class Transicion:
    serie: Serie
    apertura: Episodio | None = None
    cierre: Episodio | None = None
    recuperacion: tuple[Punto, ...] = ()


def es_finito(valor) -> bool:
    if type(valor) not in (float, int):
        return False
    try:
        return math.isfinite(valor)
    except OverflowError:
        return False


def evaluar(
    muestra: LecturaSensor, limites: LimitesSensor | None, estado: str | None,
    *, en_recuperacion: bool = False, origen: str = "mqtt",
) -> Punto:
    if muestra.ts.utcoffset() is None:
        raise ValueError("La muestra requiere timestamp con zona horaria")
    valido = es_finito(muestra.valor)
    punto = Punto(muestra.ts.astimezone(timezone.utc),
                  muestra.valor if valido or muestra.valor is None else repr(muestra.valor),
                  muestra.unidad, muestra.calidad.value, origen, estado,
                  offset_minutos=int(muestra.ts.utcoffset().total_seconds() // 60))
    if muestra.calidad == Calidad.sin_dato:
        return replace(punto, motivo="sin_dato")
    if not valido:
        return replace(punto, motivo="valor_invalido")
    if limites is None:
        return punto
    unidad = normalizar_unidad(muestra.unidad) or muestra.unidad.strip().casefold()
    canonica = normalizar_unidad(limites.unidad) or limites.unidad.strip().casefold()
    valor = muestra.valor if unidad == canonica else convertir_presion(muestra.valor, unidad, canonica)
    if valor is None:
        return replace(punto, motivo="unidad_incompatible")
    if not es_finito(valor):
        return replace(punto, motivo="valor_invalido")
    punto = replace(punto, valor_canonico=valor, unidad_canonica=limites.unidad,
                    alarma_baja=limites.baja, alarma_alta=limites.alta, disparo=limites.disparo)
    if estado == "sin_energia":
        return replace(punto, nivel="suspendido", motivo="sin_energia")
    if limites.alta is not None and valor >= limites.alta:
        return replace(punto, nivel="alta", motivo="alarma_alta")
    baja = limites.baja is not None and valor <= limites.baja
    depende_de_marcha = (
        limites.tipo_equipo == "bomba" and muestra.variable in _PRESIONES
        or limites.tipo_equipo == "filtro" and muestra.variable == "presion_diferencial"
    )
    if baja and depende_de_marcha or not baja and en_recuperacion and limites.tipo_equipo in {"bomba", "filtro"}:
        if estado in _PARADAS:
            return replace(punto, nivel="suspendido", motivo="parada_corroborada")
        if estado not in {"marcha", "disparo"} or not baja and estado == "disparo":
            return replace(punto, motivo="estado_operativo_no_corroborado")
    if baja:
        return replace(punto, nivel="baja", motivo="alarma_baja")
    return replace(punto, nivel="normal", motivo="dentro_de_limites")


def interrumpir(serie: Serie, motivo: str) -> Serie:
    return replace(serie, ventana=(), interrumpida=True, motivo=motivo)


def _evidencia_util(serie: Serie, punto: Punto) -> bool:
    return (punto.nivel == "normal" if serie.activo else punto.nivel in {"alta", "baja"})


def intervalo_historial(serie: Serie, punto: Punto, huella: str) -> tuple[datetime, datetime] | None:
    ultimo = serie.ultimo
    if (serie.ventana and not serie.interrumpida and serie.huella == huella and ultimo
            and _evidencia_util(serie, punto) and ultimo.nivel == punto.nivel
            and punto.ts - ultimo.ts > HUECO_MAXIMO):
        return max(ultimo.ts, punto.ts - HISTORIAL_MAXIMO), punto.ts
    return None


def _equivalentes(a: Punto, b: Punto) -> bool:
    if a.calidad != b.calidad:
        return False
    if a.valor_canonico is not None and b.valor_canonico is not None:
        return a.valor_canonico == b.valor_canonico and a.unidad_canonica == b.unidad_canonica
    return (a.valor, a.unidad) == (b.valor, b.unidad)


def _unir_historial(ventana: tuple[Punto, ...], punto: Punto, historial, desde: datetime) -> tuple[Punto, ...]:
    por_fecha = {punto.ts: punto}
    por_fecha.update({p.ts: p for p in ventana if p.ts >= desde})
    for p in historial:
        if not desde <= p.ts <= punto.ts:
            return ()
        previo = por_fecha.get(p.ts)
        if previo is not None:
            if not _equivalentes(previo, p):
                return ()
        else:
            por_fecha[p.ts] = p

    puente = ventana[-1].ts == desde
    tramo = [punto]
    for anterior in sorted(por_fecha.values(), key=lambda p: p.ts, reverse=True)[1:]:
        if anterior.nivel != punto.nivel or tramo[-1].ts - anterior.ts > HUECO_MAXIMO:
            if puente:
                return ()
            break
        tramo.append(anterior)
    anteriores = ventana[:-1] if puente else ()
    return anteriores + tuple(reversed(tramo))


def avanzar(
    serie: Serie, punto: Punto, *, epoca: str, clave: tuple[str, str], huella: str,
    historial: tuple[Punto, ...] = (),
) -> Transicion:
    ultimo = serie.ultimo
    if ultimo is not None and punto.ts <= ultimo.ts:
        previo = ultimo if punto.ts == ultimo.ts else next((p for p in serie.ventana if p.ts == punto.ts), None)
        if previo and not _equivalentes(previo, punto):
            return Transicion(interrumpir(serie, "muestra_contradictoria"))
        if punto.ts < ultimo.ts:
            return Transicion(replace(serie, motivo="muestra_tardia"))
        return Transicion(serie)

    ventana = ()
    motivo = punto.motivo
    if _evidencia_util(serie, punto):
        ventana = (punto,)
        if serie.huella == huella and serie.ventana and serie.ventana[-1].nivel == punto.nivel:
            if punto.ts - ultimo.ts <= HUECO_MAXIMO:
                ventana = serie.ventana + (punto,)
            else:
                intervalo = intervalo_historial(serie, punto, huella)
                if intervalo:
                    verificada = _unir_historial(serie.ventana, punto, historial, intervalo[0])
                    ventana = verificada or ventana
                    if len(ventana) == 1:
                        motivo = "hueco_sin_cobertura"
    activo = serie.activo
    if activo and punto.nivel in {"alta", "baja"}:
        activo = replace(activo, minimo=min(activo.minimo, punto.valor_canonico),
                         maximo=max(activo.maximo, punto.valor_canonico))
    if len(ventana) > MAX_PUNTOS:
        ventana = ventana[:2] + ventana[-(MAX_PUNTOS - 2):]
    nueva = Serie(punto, ventana, activo, huella, motivo)
    if len(ventana) < MIN_MUESTRAS or punto.ts - ventana[0].ts < DURACION:
        return Transicion(nueva)
    if activo is not None:
        return Transicion(replace(nueva, ventana=(), activo=None), cierre=activo, recuperacion=ventana)
    inicio = ventana[0].ts
    identidad = _huella([epoca, "sensor", *clave, punto.nivel, inicio.isoformat()])
    valores = [p.valor_canonico for p in ventana]
    episodio = Episodio(f"B1-SENSOR-{identidad}", punto.nivel, inicio, punto.ts,
                        ventana, min(valores), max(valores))
    return Transicion(replace(nueva, ventana=(), activo=episodio), apertura=episodio)
