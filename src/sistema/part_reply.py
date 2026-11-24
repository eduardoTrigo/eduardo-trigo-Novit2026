"""Interpreta respuestas de tablet contra las filas de un borrador vigente."""

import re
from collections.abc import Callable

from planta_contrato import FilaParte

from sistema.part_extraction import (
    indexar_aliases,
    interpretar_medicion,
    interpretar_numero,
    normalizar_texto,
    normalizar_unidad,
    unidad_compatible,
)


_CONFIRMACIONES = {
    "confirmo", "confirmo el parte", "si confirmo", "esta bien confirmo",
    "correcto dale", "si asi es confirmo", "perfecto confirmo",
}
_RECHAZOS = {
    "rechazo el parte", "rechazalo", "descarta el parte", "descartalo",
    "cancela el parte", "no lo guardes", "no guardes el parte",
}
_CIERRES = {"corregilo y te confirmo", "arregla eso", "con eso esta"}
_FRASES = re.compile(r"(?<!\d)\.|\.(?!\d)|[!?;\n]+")
_CAMBIO_EQUIPO = re.compile(
    r"eso de (?P<valor>.+?) era de (?P<nuevo>.+?),\s*no de (?P<equipo>.+)"
)
_CAMBIO_UNIDAD = re.compile(
    r"lo de (?P<equipo>.+?) es en (?P<nueva>.+?),\s*no en (?P<anterior>.+)"
)
_CAMBIO_VALOR = re.compile(
    r"(?P<equipo>.+?) (?:estaba|esta) en (?P<nueva>.+?)(?:,\s*no en (?P<anterior>.+))?"
)


def _equipo(nombre, aliases):
    equipos = aliases.get(nombre.strip(), {})
    return next(iter(equipos.values())) if len(equipos) == 1 else None


def _cambiar_equipo(m, filas, aliases):
    previo = _equipo(m["equipo"], aliases)
    nuevo = _equipo(m["nuevo"], aliases)
    valor = interpretar_numero(m["valor"], decimal_implicito=True)
    if previo is None or nuevo is None or previo["tipo"] != nuevo["tipo"] or valor is None:
        return None
    candidatos = [f for f in filas if f.tag == previo["tag"] and f.valor == valor]
    if len(candidatos) != 1:
        return None
    fila = candidatos[0]
    return fila, {"tag": nuevo["tag"]}


def _cambiar_unidad(m, filas, aliases):
    equipo = _equipo(m["equipo"], aliases)
    nueva = normalizar_unidad(m["nueva"])
    anterior = normalizar_unidad(m["anterior"])
    if equipo is None or nueva is None or anterior is None or not unidad_compatible(anterior, nueva):
        return None
    candidatos = [f for f in filas if f.tag == equipo["tag"] and f.unidad == anterior and f.valor is not None]
    if len(candidatos) != 1:
        return None
    return candidatos[0], {"unidad": nueva}


def _cambiar_valor(m, filas, aliases):
    equipo = _equipo(m["equipo"], aliases)
    if equipo is None:
        return None
    cambios = []
    for fila in filas:
        if fila.tag != equipo["tag"] or fila.valor is None or fila.unidad is None:
            continue
        medida = interpretar_medicion(m["nueva"], fila.variable)
        if medida is None:
            continue
        valor, unidad = medida
        if unidad is not None and not unidad_compatible(fila.unidad, unidad):
            continue
        if m["anterior"] is not None:
            anterior = interpretar_medicion(m["anterior"], fila.variable)
            if anterior not in {(fila.valor, None), (fila.valor, fila.unidad)}:
                continue
        cambios.append((fila, {"valor": valor, "unidad": unidad or fila.unidad}))
    return cambios[0] if len(cambios) == 1 else None


def _decision(texto: str) -> str | None:
    if "?" in texto:
        return None
    frase = " ".join(re.findall(r"\w+", texto))
    if frase in _CONFIRMACIONES:
        return "confirmada"
    contenido = re.sub(r"^no[.,]\s*", "", texto)
    frase_sin_prefijo = " ".join(re.findall(r"\w+", contenido))
    if frase in _RECHAZOS or frase_sin_prefijo in _RECHAZOS:
        return "rechazada"
    return None


def interpretar_respuesta(
    texto: str, filas: list[FilaParte], catalogo: Callable[[], dict]
) -> tuple[str, list[FilaParte] | None]:
    texto = " ".join(normalizar_texto(texto).split())
    decision = _decision(texto)
    if decision is not None:
        return decision, None
    if not filas:
        return "insuficiente", None

    contenido = re.sub(r"^no[.,]\s*", "", texto)
    oraciones = [x.strip() for x in _FRASES.split(contenido) if x.strip()]
    if oraciones and oraciones[-1] in _CIERRES:
        oraciones.pop()
    patrones = (
        (_CAMBIO_EQUIPO, _cambiar_equipo),
        (_CAMBIO_UNIDAD, _cambiar_unidad),
        (_CAMBIO_VALOR, _cambiar_valor),
    )
    if not oraciones or "?" in texto:
        return "insuficiente", None

    rectificaciones = []
    for oracion in oraciones:
        for patron, aplicar in patrones:
            m = patron.fullmatch(oracion)
            if m:
                rectificaciones.append((m, aplicar))
                break
        else:
            return "insuficiente", None

    aliases = indexar_aliases(catalogo())
    nuevas = list(filas)
    modificadas = set()
    for m, aplicar in rectificaciones:
        cambio = aplicar(m, filas, aliases)
        if cambio is None:
            return "insuficiente", None
        fila, campos = cambio
        i = filas.index(fila)
        if i in modificadas:
            return "insuficiente", None
        modificadas.add(i)
        nuevas[i] = FilaParte.model_validate(fila.model_dump() | campos)

    if nuevas == filas or len({(f.tag, f.variable) for f in nuevas}) != len(nuevas):
        return "insuficiente", None
    return "corregida", nuevas
