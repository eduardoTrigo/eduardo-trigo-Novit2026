
"""Prueba controlada de integración MQTT + Worker PIA-3.

Este programa no transcribe audios ni publica alertas.
Solo comprueba recepción, validación y encolado.
"""

import logging

from planta_contrato import NotaRecorrida

from sistema.mqtt_client import conectar_mqtt
from sistema.security_worker import WorkerSeguridad


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def procesar_nota_prueba(nota: NotaRecorrida) -> bool:
    """Simula el procesamiento sin generar alertas."""

    logger.info(
        "WORKER | Procesando nota: %s",
        nota.id,
    )

    logger.info(
        "WORKER | Operador: %s",
        nota.operador,
    )

    logger.info(
        "WORKER | Audio: %s",
        nota.audio_url,
    )

    logger.info(
        "WORKER | Nota %s procesada correctamente",
        nota.id,
    )

    return True


def main():
    logger.info("Iniciando prueba MQTT + Worker PIA-3")

    worker = WorkerSeguridad(
        procesar=procesar_nota_prueba,
        capacidad=100,
    )

    worker.iniciar()

    cliente = None

    try:
        cliente = conectar_mqtt(
            encolar_nota=worker.encolar
        )

        logger.info("Esperando notas reales de PIA-3...")

        cliente.loop_forever()

    except KeyboardInterrupt:
        logger.info("Prueba detenida por el usuario")

    except Exception:
        logger.exception("Error durante la integración")

    finally:
        if cliente is not None:
            cliente.disconnect()

        if not worker.detener(timeout=5):
            logger.warning(
                "El worker no terminó dentro del plazo"
            )

        logger.info("Finalizando prueba")


if __name__ == "__main__":
    main()
