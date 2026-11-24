
"""Procesamiento de seguridad con finalización transaccional."""

import logging
from collections.abc import Callable

from planta_contrato import NotaRecorrida

from sistema.note_store import AlmacenNotas
from sistema.security_pipeline import analizar_nota
from sistema.security_messages import crear_mensajes_seguridad
from sistema.security_transaction import completar_con_alertas
from sistema.transcription_service import MotorTranscripcion

logger = logging.getLogger(__name__)


def crear_procesador_persistente(
    almacen: AlmacenNotas,
    motor: MotorTranscripcion,
) -> Callable[[NotaRecorrida], bool]:
    """Construye el procesador del worker persistente."""

    def procesar(nota: NotaRecorrida) -> bool:
        if not nota.audio_url:
            logger.error("Nota %s sin audio", nota.id)
            return False

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

            eventos, tablet = crear_mensajes_seguridad(
                nota,
                resultado.condiciones,
            )

            completada = completar_con_alertas(
                almacen=almacen,
                nota_id=nota.id,
                eventos=eventos,
                tablet=tablet,
                transcripcion=resultado.texto,
            )

            if not completada:
                logger.error(
                    "No se pudo completar la nota %s",
                    nota.id,
                )
                return False

            logger.info(
                "Nota %s completada | Alertas guardadas: %s",
                nota.id,
                len(eventos),
            )

            return True

        except Exception:
            logger.exception(
                "Error procesando nota %s",
                nota.id,
            )
            return False

    return procesar
