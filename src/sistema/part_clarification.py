"""Preguntas de tablet y resolución de una duda sobre una lectura."""

import re

from planta_contrato import FilaParte, MensajeTablet, NotaRecorrida

from sistema.part_extraction import (
    duda_por_conflicto,
    indexar_aliases,
    interpretar_medicion,
    lectura_del_equipo,
    lecturas_equivalentes,
    normalizar_texto,
    normalizar_unidad,
    unidad_de_variable,
)
from sistema.part_messages import crear_aclaracion
from sistema.part_reply import interpretar_respuesta


_VARIABLES = {
    "presion_descarga_local": "descarga",
    "presion_succion": "succión",
    "presion_diferencial": "diferencial",
    "temperatura_rodamiento_ir": "temperatura del rodamiento",
    "temperatura_motor_ir": "temperatura del motor",
    "nivel": "nivel",
    "caudal": "caudal",
}
_CIERRE_VIRTUAL = re.compile(r"\.\s*(?:corregilo y te confirmo|arregla eso|con eso esta)\.?$")


def _numero(valor: float) -> str:
    return f"{valor:g}".replace(".", ",")


def _fila(duda: dict) -> FilaParte:
    return FilaParte(tag=duda["tag"], variable=duda["variable"], valor=duda["valor"], unidad=duda["unidad"])


def preguntar(
    nota: NotaRecorrida, duda: dict, equipos: dict, filas: list[FilaParte], version: int,
) -> MensajeTablet:
    tipo = duda["tipo"]
    opciones = []
    mostradas = []
    if tipo == "equipo":
        texto = f'¿A qué equipo corresponde la lectura «{duda["clausula"]}»?'
        for tag in duda["opciones"]:
            nombres = equipos[tag].get("nombres", [])
            opciones.append(f"{tag} · {nombres[0]}" if nombres else tag)
    elif tipo == "variable":
        texto = f'¿Qué variable mediste en {duda["tag"]} para la lectura «{duda["clausula"]}»?'
    elif tipo == "unidad":
        variable = _VARIABLES[duda["variable"]]
        texto = f'¿En qué unidad está la lectura de {variable} de {duda["tag"]}: {_numero(duda["valor"])}?'
    elif tipo == "valor":
        alternativas = [_numero(v) for v in duda["alternativas"]]
        texto = f'¿Cuánto marcó el diferencial de {duda["tag"]}: {alternativas[0]} o {alternativas[1]}?'
        if duda.get("confirmar_candidato"):
            opciones = alternativas
            provisional = _fila(duda)
            mostradas = [f for f in filas if (f.tag, f.variable) != (provisional.tag, provisional.variable)]
            mostradas.append(provisional)
            texto += (
                f' Confirmá solo si fueron {alternativas[0]} {duda["unidad"]}.'
                f" Si fueron {alternativas[1]}, indicá el valor correcto."
            )
    elif tipo == "conflicto":
        opciones = [f'{_numero(f["valor"])} {f["unidad"]}' for f in duda["alternativas"]]
        texto = f'¿Cuál fue la lectura de {_VARIABLES[duda["variable"]]} de {duda["tag"]}: '
        texto += " o ".join(opciones) + "?"
    else:
        fila = FilaParte.model_validate(duda["fila"])
        valor = _numero(fila.valor)
        texto = (
            f'La lectura de {_VARIABLES[fila.variable]} de {fila.tag} ({valor} {fila.unidad}) '
            "está fuera del rango consultado. ¿Confirmás ese valor o lo corregís?"
        )
        opciones = ["Confirmar lectura", "Corregir lectura"]
        mostradas = filas
    return crear_aclaracion(nota, texto, version=version, opciones=opciones, filas=mostradas)


def _equipo_de_respuesta(nombre, alias):
    equipos = alias.get(nombre, {})
    return next(iter(equipos)) if len(equipos) == 1 else None


def _medida_respuesta(texto, duda, alias):
    texto = _CIERRE_VIRTUAL.sub("", texto)
    texto = re.sub(r"^no[.,]\s*", "", texto)
    con_equipo = re.fullmatch(r"(.+?) (?:esta|estaba) en (.+?)(?:,\s*no en (.+))?", texto)
    if con_equipo:
        nombre, texto, anterior = con_equipo.groups()
        if _equipo_de_respuesta(nombre, alias) != duda["tag"]:
            return None
        if anterior is not None:
            anterior = interpretar_medicion(anterior, duda["variable"], valor_explicito=True)
            if anterior is None or anterior[0] != duda["valor"]:
                return None
    return interpretar_medicion(texto, duda["variable"], valor_explicito=True)


def resolver(duda: dict, texto: str, catalogo: dict, filas: list[FilaParte]):
    """Devuelve el resultado, las filas resueltas y una duda sucesora si corresponde."""
    tipo = duda["tipo"]
    if tipo == "rango":
        resultado, nuevas = interpretar_respuesta(texto, filas, lambda: catalogo)
        opcion = normalizar_texto(texto).strip().rstrip(".!")
        if resultado == "confirmada" or opcion == "confirmar lectura":
            return "resuelta", filas, None
        if resultado == "corregida":
            fila = FilaParte.model_validate(duda["fila"])
            if fila not in nuevas:
                return resultado, nuevas, None
        return "insuficiente", filas, None

    texto = " ".join(normalizar_texto(texto).split()).rstrip(".!")
    if not texto or "?" in texto:
        return "insuficiente", filas, None
    alias = indexar_aliases(catalogo)
    equipos = {e["tag"]: e for e in catalogo["equipos"]}
    siguiente = None
    fila = None
    if tipo in {"equipo", "variable"}:
        clausula = duda["clausula"]
        if tipo == "equipo":
            nombre = re.sub(r"^(?:era|es|la correcta es)\s+", "", texto)
            tag_dicho, separador, nombre_dicho = nombre.partition(" · ")
            tag = _equipo_de_respuesta(tag_dicho, alias)
            if separador and tag not in alias.get(nombre_dicho, {}):
                return "insuficiente", filas, None
            if tag not in duda["opciones"] or tag not in equipos:
                return "insuficiente", filas, None
        else:
            tag = duda["tag"]
            variable = re.sub(r"^(?:era|es)\s+", "", texto)
            if variable not in {normalizar_texto(v) for v in _VARIABLES.values()}:
                return "insuficiente", filas, None
            clausula = f"{clausula} de {variable}"
        fila, pendiente = lectura_del_equipo(clausula, equipos[tag], catalogo)
        if isinstance(pendiente, dict):
            siguiente = pendiente
        elif tipo == "equipo" and fila is None:
            siguiente = {"tipo": "variable", "tag": tag, "clausula": clausula}
    elif tipo == "unidad":
        con_equipo = re.fullmatch(r"(.+?) (?:esta|estaba) en (.+)", texto)
        if con_equipo:
            if _equipo_de_respuesta(con_equipo[1], alias) != duda["tag"]:
                return "insuficiente", filas, None
            texto = con_equipo[2]
        unidad = normalizar_unidad(re.sub(r"^en\s+", "", texto))
        if unidad is not None and unidad_de_variable(duda["variable"], unidad):
            fila = _fila(duda | {"unidad": unidad})
    elif tipo == "conflicto":
        medida = interpretar_medicion(texto, duda["variable"], valor_explicito=True)
        if medida is not None:
            valor, unidad = medida
            coincidencias = [f for f in duda["alternativas"]
                             if f["valor"] == valor and unidad in {None, f["unidad"]}]
            if len(coincidencias) == 1:
                fila = FilaParte.model_validate(coincidencias[0])
    elif tipo == "valor":
        decision, _ = interpretar_respuesta(texto, [], lambda: catalogo)
        if decision == "confirmada" and duda.get("confirmar_candidato"):
            medida = duda["valor"], duda["unidad"]
        else:
            medida = _medida_respuesta(texto, duda, alias)
        if medida is not None:
            valor, explicita = medida
            unidad = explicita or duda["unidad"]
            if valor in duda["alternativas"] and (unidad is None or unidad_de_variable(duda["variable"], unidad)):
                lect = duda | {"valor": valor, "unidad": unidad}
                if unidad is None:
                    siguiente = lect | {"tipo": "unidad"}
                else:
                    fila = _fila(lect)

    if siguiente is not None:
        return "resuelta", filas, siguiente
    if fila is None:
        return "insuficiente", filas, None
    anterior = next((f for f in filas if (f.tag, f.variable) == (fila.tag, fila.variable)), None)
    if anterior is not None:
        if lecturas_equivalentes(anterior, fila):
            return "resuelta", filas, None
        if anterior.valor is not None and fila.valor is not None:
            return "resuelta", [f for f in filas if f != anterior], duda_por_conflicto([anterior, fila])
        return "conflicto_lecturas", filas, None
    return "resuelta", [*filas, fila], None
