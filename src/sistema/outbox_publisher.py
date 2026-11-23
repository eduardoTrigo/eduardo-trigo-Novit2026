
"""Publicación MQTT de mensajes almacenados en la outbox."""

import logging

import paho.mqtt.client as mqtt

from sistema.security_outbox import OutboxSeguridad

logger = logging.getLogger(__name__)


def publicar_siguiente(
    outbox: OutboxSeguridad,
    cliente: mqtt.Client,
    timeout: float = 5.0,
) -> bool:
    """Intenta publicar un mensaje pendiente con QoS 1.

    Devuelve True solamente cuando MQTT confirma
    la publicación y SQLite registra su finalización.
    """

    if not cliente.conectado.is_set():
        logger.warning("MQTT desconectado; envío pospuesto")
        return False

    mensaje = outbox.reclamar()

    if mensaje is None:
        return False

    identificador = mensaje["id"]

    try:
        resultado = cliente.publish(
            topic=mensaje["topico"],
            payload=mensaje["payload"],
            qos=1,
        )

        if resultado.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(
                f"MQTT rechazó publicación: {resultado.rc}"
            )

        # Esperar acuse de publicación QoS 1.
        resultado.wait_for_publish(timeout=timeout)

        if not resultado.is_published():
            raise TimeoutError(
                f"Sin confirmación MQTT: {identificador}"
            )

        if not outbox.confirmar(identificador):
            raise RuntimeError(
                f"No se pudo confirmar en SQLite: {identificador}"
            )

        logger.info(
            "Publicación confirmada | ID: %s | Tópico: %s",
            identificador,
            mensaje["topico"],
        )

        return True

    except Exception as error:
        logger.exception(
            "Fallo al publicar %s",
            identificador,
        )

        try:
            outbox.fallar(identificador, str(error))
        except Exception:
            logger.exception(
                "No se pudo registrar el fallo de %s",
                identificador,
            )

        return False

