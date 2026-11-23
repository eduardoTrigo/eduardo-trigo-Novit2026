
"""Prueba real MQTT + SQLite + Worker + STT + Seguridad.

No publica alertas ni mensajes de tablet.
"""

import logging
import os
import time
from pathlib import Path

from planta_contrato import NotaRecorrida

from sistema.mqtt_client import conectar_mqtt
from sistema.note_store import AlmacenNotas
from sistema.persistent_ingress import crear_entrada_persistente
from sistema.persistent_worker import WorkerPersistente
from sistema.security_pipeline import analizar_nota
from sistema.whisper_adapter import WhisperLocal


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    logger.info("Iniciando integración persistente PIA-3")
    logger.info("MODO SEGURO: sin publicaciones de seguridad")

    # Almacenamiento en disco.
    ruta_db = Path(
        os.getenv(
            "PIA3_DB_PATH",
            "data_runtime/notas.sqlite3",
        )
    )

    almacen = AlmacenNotas(ruta_db)

    logger.info("Base de datos: %s", ruta_db)
    logger.info(
        "Notas pendientes: %s",
        almacen.pendientes(),
    )

    # Cargar Whisper solamente una vez.
    motor = WhisperLocal(modelo="base")

    def procesar_nota(nota: NotaRecorrida) -> bool:
        inicio = time.perf_counter()

        logger.info("Procesando: %s", nota.id)

        try:
            resultado = analizar_nota(
                audio_url=nota.audio_url,
                motor=motor,
            )

            logger.info(
                "TRANSCRIPCION | %s | %s",
                nota.id,
                resultado.texto,
            )

            for condicion in resultado.condiciones:
                logger.warning(
                    "RIESGO | %s | %s | %s",
                    nota.id,
                    condicion.codigo,
                    condicion.descripcion,
                )

            if not resultado.condiciones:
                logger.info(
                    "Sin riesgos | %s",
                    nota.id,
                )

            logger.info(
                "Tiempo de procesamiento: %.2f s",
                time.perf_counter() - inicio,
            )

            return True

        except Exception:
            logger.exception(
                "Error procesando nota %s",
                nota.id,
            )
            return False

    worker = WorkerPersistente(
        almacen=almacen,
        procesar=procesar_nota,
        intervalo=0.5,
        espera_error=2.0,
    )

    # El callback MQTT solamente guarda.
    entrada = crear_entrada_persistente(almacen)

    cliente = None

    try:
        worker.iniciar()

        cliente = conectar_mqtt(
            encolar_nota=entrada,
        )

        logger.info(
            "Escuchando notas reales de PIA-3"
        )

        cliente.loop_forever()

    except KeyboardInterrupt:
        logger.info("Deteniendo integración")

    except Exception:
        logger.exception("Error de integración")

    finally:
        if cliente is not None:
            cliente.disconnect()

        if not worker.detener(timeout=30):
            logger.warning(
                "Worker no detenido completamente"
            )

        logger.info(
            "Notas pendientes: %s",
            almacen.pendientes(),
        )

        logger.info("Prueba finalizada")


if __name__ == "__main__":
    main()
