
"""Entrada persistente de notas MQTT para PIA-3."""

import logging
from collections.abc import Callable

from planta_contrato import NotaRecorrida

from sistema.note_store import AlmacenNotas

logger = logging.getLogger(__name__)


def crear_entrada_persistente(
    almacen: AlmacenNotas,
) -> Callable[[NotaRecorrida], bool]:
    """Crea un callback que guarda notas en SQLite."""

    def recibir_nota(nota: NotaRecorrida) -> bool:
        try:
            nueva = almacen.guardar(nota)

            if nueva:
                logger.info(
                    "Nota %s guardada en SQLite",
                    nota.id,
                )
            else:
                logger.info(
                    "Nota %s ya registrada; duplicado ignorado",
                    nota.id,
                )

            # Una nota duplicada ya se encuentra almacenada.
            # Por eso también se considera aceptada.
            return True

        except Exception:
            logger.exception(
                "No se pudo persistir la nota %s",
                nota.id,
            )
            return False

    return recibir_nota
