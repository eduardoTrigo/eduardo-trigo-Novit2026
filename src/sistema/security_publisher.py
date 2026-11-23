
"""Publicación MQTT de alertas de seguridad PIA-3."""

import logging

import paho.mqtt.client as mqtt

from planta_contrato import (
    EventoSistema,
    MensajeTablet,
    Topicos,
)

logger = logging.getLogger(__name__)


def publicar_mensaje(
    cliente: mqtt.Client,
    topico: str,
    mensaje: EventoSistema | MensajeTablet,
) -> bool:
    """Solicita una publicación MQTT con QoS 1."""

    if not cliente.conectado.is_set():
        logger.error(
            "No se puede publicar: MQTT desconectado"
        )
        return False

    resultado = cliente.publish(
        topic=topico,
        payload=mensaje.model_dump_json(),
        qos=1,
    )

    if resultado.rc != mqtt.MQTT_ERR_SUCCESS:
        logger.error(
            "Error publicando en %s: %s",
            topico,
            resultado.rc,
        )
        return False

    logger.info(
        "Publicación solicitada | Tópico: %s | ID: %s",
        topico,
        mensaje.id,
    )

    return True


def publicar_alertas_seguridad(
    cliente: mqtt.Client,
    eventos: list[EventoSistema],
    tablet: MensajeTablet | None,
) -> bool:
    """Solicita publicaciones de eventos y aviso de tablet."""

    if not eventos:
        return tablet is None

    if tablet is None:
        logger.error("Falta el aviso de seguridad para tablet")
        return False

    for evento in eventos:
        if not publicar_mensaje(
            cliente,
            Topicos.EVENTOS,
            evento,
        ):
            return False

    return publicar_mensaje(
        cliente,
        Topicos.TABLET,
        tablet,
    )
