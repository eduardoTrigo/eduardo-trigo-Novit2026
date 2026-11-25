"""Reglas B1 para filas estructuradas de un parte."""

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal

from planta_contrato import FilaParte

from sistema.part_extraction import convertir_presion, normalizar_texto, normalizar_unidad


ResultadoB1 = Literal["SinNovedad", "Escalado", "Indeterminado"]


@dataclass(frozen=True)
class EvidenciaB1:
    variable: str
    valor: float | str | None
    unidad: str | None
    observacion: str | None
    motivo: str
    resultado: ResultadoB1 | None = None
    variable_canonica: str | None = None
    valor_canonico: float | None = None
    unidad_canonica: str | None = None
    alarma_baja: float | None = None
    alarma_alta: float | None = None
    disparo: float | None = None
    hallazgo: str | None = None


@dataclass(frozen=True)
class DecisionB1:
    equipo: str
    resultado: ResultadoB1
    evidencia: tuple[EvidenciaB1, ...]


_VARIABLE_SENSOR = {
    "presion_descarga_local": "presion_descarga",
    "presion_diferencial_local": "presion_diferencial",
}
_PRESIONES_DE_BOMBA = {"presion_succion", "presion_descarga"}
_PARADAS = {"reserva", "mantenimiento"}
_OPERACION = _PARADAS | {"marcha"}
_PATRONES = {
    "estado": {
        "reserva": r"parad[ao]\s+en reserva",
        "mantenimiento": r"parad[ao]\s+por mantenimiento",
        "marcha": r"en marcha",
        "disparo": r"(?:disparad[ao]|no arranca)",
    },
    "ruido": {
        "golpeteo": r"golpeteo metalico",
        "cavitacion": r"(?:suena como si tuviera piedras adentro|cavitacion)",
        "ruido_rodamiento": r"ruido (?:raro|anormal) de rodamiento",
    },
    "goteo_empaquetadura": {
        "perdida_empaquetadura": (
            r"(?:pierde bastante|chorrea|perdida importante)(?: por)? (?:la )?empaquetadura"
        ),
        "goteo_sin_calificar": r"(?:gotea|goteando|goteo)(?: por)? (?:la )?empaquetadura",
    },
    "nivel_aceite_visor": {
        "aceite_bajo": r"(?:nivel de aceite bajo|visor de aceite esta bajo|casi no tiene aceite en el visor)",
    },
    "estado_correas": {
        "correas_anomalas": r"correas (?:(?P<negacion>no) )?(?:estan )?(?:flojas|danadas|rotas|chillan)",
    },
    "temperatura_rodamiento_ir": {"rodamiento_caliente": r"rodamiento (?:esta )?caliente"},
    "nivel": {"rebalse": r"(?:rebalse|rebalsando) a (?:la )?pileta"},
    "presion_diferencial": {"diferencial_alto": r"diferencial alto"},
}
_SEPARACION = re.compile(r"([,.;!?\n]+|\b(?:y|pero|ni)\b)")
_NEGACION = re.compile(r"\b(?:no|sin|nunca)\b")
_DUDA = re.compile(r"\b(?:no se si|no solo|quizas|tal vez|parece|creo|posible|podria|ayer|si)\b")


def _evidencia(fila: FilaParte, motivo: str, resultado: ResultadoB1 | None, **datos) -> EvidenciaB1:
    valor = fila.valor
    if valor is not None and not _finito(valor):
        # Forma estable y serializable para valores rechazados, incluido NaN.
        valor = repr(valor)
    return EvidenciaB1(
        fila.variable, valor, fila.unidad, fila.observacion, motivo, resultado, **datos,
    )


def _finito(valor: object) -> bool:
    return type(valor) is int or (type(valor) is float and math.isfinite(valor))


def _unidad(unidad: str) -> str:
    return normalizar_unidad(unidad) or normalizar_texto(unidad.strip())


def _interpretar_clausula(
    texto: str, patrones: Mapping[str, str],
) -> Iterable[tuple[str | None, bool | None]]:
    coincidencias = [
        (coincidencia, hallazgo)
        for hallazgo, patron in patrones.items()
        for coincidencia in re.finditer(rf"\b(?:{patron})\b", texto)
    ]
    if not coincidencias:
        yield None, None
        return

    # La frase de cavitación incluye «como si» como descripción del sonido.
    duda = _DUDA.search(texto.replace("como si", "como"))
    fin_anterior = 0
    for coincidencia, hallazgo in sorted(coincidencias, key=lambda par: par[0].start()):
        prefijo = texto[fin_anterior:coincidencia.start()]
        negado = coincidencia.groupdict().get("negacion") or _NEGACION.search(prefijo)
        fin_anterior = coincidencia.end()
        yield hallazgo, None if duda else not negado


def _observaciones(fila: FilaParte) -> Iterable[EvidenciaB1]:
    texto = normalizar_texto(fila.observacion or "").strip()
    texto = re.sub(r"\b(parad[ao]),\s*(en reserva)\b", r"\1 \2", texto)
    patrones = _PATRONES.get(_VARIABLE_SENSOR.get(fila.variable, fila.variable), {})
    partes = _SEPARACION.split(texto)
    anterior_negada = False
    for union, clausula in zip(["", *partes[1::2]], partes[::2]):
        if not clausula.strip():
            continue
        for hallazgo, afirmado in _interpretar_clausula(clausula, patrones):
            if union == "ni" and afirmado is True:
                afirmado = False
            elif union == "y" and anterior_negada and afirmado is True:
                afirmado = None
            union = ""
            anterior_negada = afirmado is False
            if afirmado is None or (afirmado and hallazgo == "goteo_sin_calificar"):
                yield _evidencia(fila, "observacion_no_resuelta", "Indeterminado", hallazgo=hallazgo)
            elif not afirmado and hallazgo in _OPERACION:
                yield _evidencia(fila, "estado_operativo_negado", "Indeterminado", hallazgo=hallazgo)
            elif not afirmado:
                yield _evidencia(fila, "hallazgo_negado", "SinNovedad", hallazgo=hallazgo)
            elif hallazgo in _OPERACION:
                resultado = "SinNovedad" if hallazgo in _PARADAS else None
                yield _evidencia(fila, "estado_operativo_informado", resultado, hallazgo=hallazgo)
            else:
                yield _evidencia(fila, "hallazgo_afirmado", "Escalado", hallazgo=hallazgo)


def _contradicciones(observaciones: list[EvidenciaB1]) -> set[str]:
    estados = {e.hallazgo for e in observaciones if e.motivo == "estado_operativo_informado"}
    negados = {e.hallazgo for e in observaciones if e.motivo in {"hallazgo_negado", "estado_operativo_negado"}}
    afirmados = estados | {e.hallazgo for e in observaciones if e.motivo == "hallazgo_afirmado"}
    conflictos = afirmados & negados
    if "marcha" in estados and estados & _PARADAS:
        conflictos.update(estados)
    if "marcha" in estados and "disparo" in afirmados:
        conflictos.update({"marcha", "disparo"})
    return conflictos


def _lectura(fila: FilaParte, sensores: list | None, parada: bool, estado_en_conflicto: bool) -> EvidenciaB1:
    variable = _VARIABLE_SENSOR.get(fila.variable, fila.variable)
    datos = {"variable_canonica": variable}
    if fila.valor is None:
        return _evidencia(fila, "valor_ausente", "Indeterminado", **datos)
    if not _finito(fila.valor):
        return _evidencia(fila, "valor_invalido", "Indeterminado", **datos)
    if not fila.unidad or not fila.unidad.strip():
        return _evidencia(fila, "unidad_ausente", "Indeterminado", **datos)
    if sensores is None:
        return _evidencia(fila, "catalogo_ausente", "Indeterminado", **datos)
    if not sensores:
        return _evidencia(fila, "sin_limites", None, **datos)
    if len(sensores) != 1:
        return _evidencia(fila, "limites_invalidos", "Indeterminado", **datos)

    sensor = sensores[0]
    unidad = sensor.get("unidad")
    baja, alta, disparo = (sensor.get(campo) for campo in ("alarma_baja", "alarma_alta", "disparo"))
    if (not isinstance(unidad, str) or not unidad.strip()
            or any(v is not None and not _finito(v) for v in (baja, alta, disparo))
            or (baja is not None and alta is not None and baja >= alta)):
        return _evidencia(fila, "limites_invalidos", "Indeterminado", **datos)
    origen, canonica = _unidad(fila.unidad), _unidad(unidad)
    valor = fila.valor if origen == canonica else convertir_presion(fila.valor, origen, canonica)
    datos.update(unidad_canonica=unidad, alarma_baja=baja, alarma_alta=alta, disparo=disparo)
    if valor is None:
        return _evidencia(fila, "unidad_incompatible", "Indeterminado", **datos)
    if not _finito(valor):
        return _evidencia(fila, "valor_invalido", "Indeterminado", **datos)
    datos["valor_canonico"] = valor
    if baja is None and alta is None:
        return _evidencia(fila, "sin_limites", None, **datos)
    if alta is not None and valor >= alta:
        return _evidencia(fila, "alarma_alta", "Escalado", **datos)
    if baja is not None and valor <= baja:
        if variable in _PRESIONES_DE_BOMBA:
            if estado_en_conflicto:
                return _evidencia(fila, "aplicabilidad_indeterminada", "Indeterminado", **datos)
            if parada:
                return _evidencia(fila, "baja_esperada_por_parada", "SinNovedad", **datos)
        return _evidencia(fila, "alarma_baja", "Escalado", **datos)
    return _evidencia(fila, "dentro_de_limites", "SinNovedad", **datos)


def _clasificar_equipo(tag: str, filas: list[FilaParte], sensores, tipo: str | None) -> DecisionB1:
    observaciones = [e for fila in filas for e in _observaciones(fila)]
    conflictos = _contradicciones(observaciones)
    estado_en_conflicto = tipo == "bomba" and bool(conflictos & _OPERACION)
    parada = tipo == "bomba" and any(
        e.hallazgo in _PARADAS and e.motivo == "estado_operativo_informado" for e in observaciones
    )
    evidencia = [
        replace(e, motivo="observacion_contradictoria", resultado="Indeterminado")
        if e.hallazgo in conflictos else e
        for e in observaciones
    ]
    for fila in filas:
        if fila.valor is not None or fila.unidad is not None or not (fila.observacion or "").strip():
            variable = _VARIABLE_SENSOR.get(fila.variable, fila.variable)
            limites = None if sensores is None else sensores.get((tag, variable), [])
            evidencia.append(_lectura(fila, limites, parada, estado_en_conflicto))

    resultados = {e.resultado for e in evidencia}
    if "Escalado" in resultados:
        resultado = "Escalado"
    elif "SinNovedad" in resultados and "Indeterminado" not in resultados:
        resultado = "SinNovedad"
    else:
        resultado = "Indeterminado"
    return DecisionB1(tag, resultado, tuple(evidencia))


def clasificar_parte(
    filas: Iterable[FilaParte], catalogo: Mapping | None,
) -> tuple[DecisionB1, ...]:
    """Evalúa filas por equipo usando el catálogo recibido. El resultado es interno a B1."""
    por_equipo = defaultdict(list)
    for fila in filas:
        por_equipo[fila.tag].append(fila)

    catalogo = catalogo or {}
    sensores = catalogo.get("sensores")
    limites = None
    if isinstance(sensores, (list, tuple)):
        limites = defaultdict(list)
        for sensor in sensores:
            if isinstance(sensor, Mapping):
                tag, variable = sensor.get("tag"), sensor.get("variable")
                if isinstance(tag, str) and isinstance(variable, str):
                    limites[(tag, variable)].append(sensor)

    equipos = catalogo.get("equipos")
    tipos = {}
    if isinstance(equipos, (list, tuple)):
        tipos = {e["tag"]: e.get("tipo") for e in equipos
                 if isinstance(e, Mapping) and isinstance(e.get("tag"), str)}

    decisiones = []
    for tag, filas_equipo in sorted(por_equipo.items()):
        if not tag.strip() or (tipos and tag not in tipos):
            evidencia = tuple(_evidencia(f, "equipo_desconocido", "Indeterminado") for f in filas_equipo)
            decisiones.append(DecisionB1(tag, "Indeterminado", evidencia))
        else:
            decisiones.append(_clasificar_equipo(tag, filas_equipo, limites, tipos.get(tag)))
    return tuple(decisiones)
