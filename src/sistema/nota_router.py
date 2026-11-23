
"""Recepción y validación de notas de recorrida PIA-3."""

import logging
from collections.abc import Callable

from pydantic import ValidationError
from planta_contrato import NotaRecorrida, Topicos

logger = logging.getLogger(__name__)


def recibir_nota_mqtt(
    topico: str,
    payload: bytes,
    encolar: Callable[[NotaRecorrida], bool],
) -> bool:
    """Valida una nota MQTT y solicita su ingreso a la cola."""

    if topico != Topicos.RECORRIDAS:
        return False

    try:
        nota = NotaRecorrida.model_validate_json(payload)

    except (ValidationError, ValueError):
        logger.warning(
            "Mensaje MQTT inválido en %s",
            topico,
        )
        return False

    if not nota.audio_url:
        logger.warning(
            "Nota %s sin audio: requiere tratamiento separado",
            nota.id,
        )
        return False

    try:
        aceptada = encolar(nota)

    except Exception:
        logger.exception(
            "Error al encolar nota %s",
            nota.id,
        )
        return False

    if not aceptada:
        logger.error(
            "No se pudo encolar nota %s",
            nota.id,
        )
        return False

    logger.info(
        "Nota %s entregada a la cola",
        nota.id,
    )

    return True
