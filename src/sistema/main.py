"""Punto de entrada de PIA-3."""

import logging

from sistema.mqtt_client import conectar_mqtt, iniciar_heartbeat


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    logger = logging.getLogger(__name__)
    logger.info("Iniciando sistema PIA-3...")

    cliente = conectar_mqtt()

    iniciar_heartbeat(cliente)

    try:
        cliente.loop_forever()

    except KeyboardInterrupt:
        logger.info("Deteniendo sistema PIA-3...")

    finally:
        cliente.disconnect()


if __name__ == "__main__":
    main()
