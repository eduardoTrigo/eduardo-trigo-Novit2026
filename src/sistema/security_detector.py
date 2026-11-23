
"""Detector local de condiciones inseguras para PIA-3.

Implementación determinista basada en reglas.
No utiliza LLM ni requiere conexión a internet.
"""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class CondicionInsegura:
    """Representa una condición de seguridad detectada."""

    codigo: str
    descripcion: str


PATRONES_SEGURIDAD = {
    "S1": (
        "Proyección de agua a presión",
        [
            r"\bchorro de agua a presion\b",
            r"\bagua a presion saliendo\b",
            r"\bsale agua a presion\b",
            r"\bsale un chorro de agua a presion\b",
            r"\bperdida con proyeccion\b",
            r"\bfuga con proyeccion\b",
            r"\bproyeccion de agua\b",
        ],
    ),
    "S2": (
        "Olor a gas",
        [
            r"\bolor a gas\b",
            r"\bhuele a gas\b",
            r"\bse siente olor a gas\b",
        ],
    ),
    "S3": (
        "Humo o fuego",
        [
            r"\bhay humo\b",
            r"\bsale humo\b",
            r"\bolor a quemado\b",
            r"\bhuele a quemado\b",
            r"\bse esta incendiando\b",
            r"\bhay fuego\b",
            r"\bse prendio fuego\b",
        ],
    ),
    "S4": (
        "Persona lesionada",
        [
            r"\bpersona lesionada\b",
            r"\boperador herido\b",
            r"\btecnico herido\b",
            r"\bhay un herido\b",
            r"\bpersona accidentada\b",
        ],
    ),
}


def normalizar_texto(texto: str) -> str:
    """Normaliza tildes, mayúsculas y espacios."""

    texto = texto.lower()

    texto = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )

    return re.sub(r"\s+", " ", texto).strip()


def dividir_en_segmentos(texto: str) -> list[str]:
    """Separa afirmaciones independientes de una nota."""

    return [
        segmento.strip()
        for segmento in re.split(
            r"[,;.]|\bpero\b|\bsin embargo\b",
            texto,
        )
        if segmento.strip()
    ]


def esta_negado(segmento: str, inicio: int) -> bool:
    """Detecta negaciones inmediatamente anteriores al hallazgo."""

    contexto = segmento[max(0, inicio - 45):inicio]

    patrones_negacion = [
        r"\bno\s+(?:hay\s+)?$",
        r"\bno\s+se\s+siente\s+$",
        r"\bno\s+se\s+percibe\s+$",
        r"\bsin\s+$",
        r"\bningun(?:a)?\s+$",
        r"\bno\s+hay\s+un(?:a)?\s+$",
    ]

    return any(
        re.search(patron, contexto)
        for patron in patrones_negacion
    )


def detectar_condiciones_inseguras(
    texto: str,
) -> list[CondicionInsegura]:
    """Detecta condiciones afirmativas y omite negaciones simples."""

    texto_normalizado = normalizar_texto(texto)
    segmentos = dividir_en_segmentos(texto_normalizado)

    resultados = []
    codigos_detectados = set()

    for segmento in segmentos:
        for codigo, (descripcion, patrones) in PATRONES_SEGURIDAD.items():

            for patron in patrones:
                coincidencia = re.search(patron, segmento)

                if coincidencia is None:
                    continue

                if esta_negado(segmento, coincidencia.start()):
                    continue

                if codigo not in codigos_detectados:
                    resultados.append(
                        CondicionInsegura(
                            codigo=codigo,
                            descripcion=descripcion,
                        )
                    )
                    codigos_detectados.add(codigo)

                break

    return resultados
