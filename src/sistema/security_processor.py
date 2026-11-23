
"""Orquestador del procesamiento de seguridad PIA-3."""

import logging

import paho.mqtt.client as mqtt

from planta_contrato import NotaRecorrida

from sistema.security_pipeline import analizar_nota
from sistema.security_messages import crear_mensajes_seguridad
from sistema.security_publisher import publicar_alertas_seguridad
from sistema.transcription_service import MotorTranscripcion

logger = logging.getLogger(__name__)


def procesar_nota_seguridad(
    nota: NotaRecorrida,
    motor: MotorTranscripcion,
    cliente: mqtt.Client,
) -> bool:
    """Procesa una nota y solicita alertas si detecta riesgos.

    Devuelve True si el procesamiento terminó sin errores
    y las publicaciones requeridas fueron aceptadas
    inicialmente por el cliente MQTT.
    """

    if not nota.audio_url:
        logger.warning("Nota %s sin audio", nota.id)
        return False

    try:
        resultado = analizar_nota(nota.audio_url, motor)

        logger.info(
            "Nota %s analizada: %s",
            nota.id,
            resultado.texto,
        )

        if not resultado.condiciones:
            logger.info(
                "Sin condiciones inseguras en %s",
                nota.id,
            )
            return True

        eventos, tablet = crear_mensajes_seguridad(
            nota,
            resultado.condiciones,
        )

        return publicar_alertas_seguridad(
            cliente,
            eventos,
            tablet,
        )

    except Exception:
        logger.exception(
            "Error procesando nota %s",
            nota.id,
        )
        return False
