
"""Prueba de integración real de seguridad PIA-3.

Recibe notas MQTT, descarga los audios, transcribe
con Whisper y detecta riesgos S1-S4.

Modo seguro: NO publica eventos ni avisos en tablet.
"""

import logging
import time

from planta_contrato import NotaRecorrida

from sistema.mqtt_client import conectar_mqtt
from sistema.security_pipeline import analizar_nota
from sistema.security_worker import WorkerSeguridad
from sistema.whisper_adapter import WhisperLocal


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def main():
    logger.info("Iniciando prueba real de seguridad PIA-3")
    logger.info("MODO SEGURO: publicaciones desactivadas")

    # Se carga una sola vez al iniciar.
    motor = WhisperLocal(modelo="base")

    def procesar_nota(nota: NotaRecorrida) -> bool:
        """Procesa una nota real sin publicar alertas."""

        inicio = time.perf_counter()

        logger.info(
            "Procesando nota real: %s",
            nota.id,
        )

        try:
            resultado = analizar_nota(
                audio_url=nota.audio_url,
                motor=motor,
            )

            duracion = time.perf_counter() - inicio

            logger.info(
                "TRANSCRIPCION | %s | %s",
                nota.id,
                resultado.texto,
            )

            if resultado.condiciones:
                for condicion in resultado.condiciones:
                    logger.warning(
                        "RIESGO DETECTADO | %s | %s | %s",
                        nota.id,
                        condicion.codigo,
                        condicion.descripcion,
                    )
            else:
                logger.info(
                    "Sin riesgos detectados | %s",
                    nota.id,
                )

            logger.info(
                "Procesamiento finalizado | %s | %.2f s",
                nota.id,
                duracion,
            )

            return True

        except Exception:
            logger.exception(
                "Error procesando nota %s",
                nota.id,
            )
            return False

    worker = WorkerSeguridad(
        procesar=procesar_nota,
        capacidad=100,
    )

    worker.iniciar()

    cliente = None

    try:
        cliente = conectar_mqtt(
            encolar_nota=worker.encolar,
        )

        logger.info(
            "Esperando notas reales en planta/recorridas"
        )

        cliente.loop_forever()

    except KeyboardInterrupt:
        logger.info("Deteniendo prueba")

    except Exception:
        logger.exception("Error de integración")

    finally:
        if cliente is not None:
            cliente.disconnect()

        if not worker.detener(timeout=5):
            logger.warning(
                "Worker no detenido completamente"
            )

        logger.info("Prueba finalizada")


if __name__ == "__main__":
    main()
