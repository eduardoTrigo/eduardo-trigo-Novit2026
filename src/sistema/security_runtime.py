
"""Integración del motor STT con el procesamiento de seguridad."""

import logging
from collections.abc import Callable

import paho.mqtt.client as mqtt
from planta_contrato import NotaRecorrida

from sistema.security_processor import procesar_nota_seguridad
from sistema.transcription_service import MotorTranscripcion

logger = logging.getLogger(__name__)


def crear_procesador_seguridad(
    motor: MotorTranscripcion,
    cliente: mqtt.Client,
) -> Callable[[NotaRecorrida], bool]:
    """Crea la función que utilizará WorkerSeguridad."""

    def procesar(nota: NotaRecorrida) -> bool:
        logger.info(
            "Iniciando procesamiento real de nota %s",
            nota.id,
        )

        resultado = procesar_nota_seguridad(
            nota=nota,
            motor=motor,
            cliente=cliente,
        )

        if resultado:
            logger.info(
                "Procesamiento finalizado: %s",
                nota.id,
            )
        else:
            logger.error(
                "Procesamiento incompleto: %s",
                nota.id,
            )

        return resultado

    return procesar
