
"""Worker de seguridad con tareas persistidas en SQLite."""

import logging
import threading
from collections.abc import Callable

from planta_contrato import NotaRecorrida

from sistema.note_store import AlmacenNotas

logger = logging.getLogger(__name__)


class WorkerPersistente:
    """Procesa notas almacenadas de manera persistente."""

    def __init__(
        self,
        almacen: AlmacenNotas,
        procesar: Callable[[NotaRecorrida], bool],
        intervalo: float = 0.5,
        espera_error: float = 2.0,
        finalizacion_transaccional: bool = False,
    ):
        if intervalo <= 0 or espera_error <= 0:
            raise ValueError("Los intervalos deben ser positivos")

        self.almacen = almacen
        self.procesar = procesar
        self.intervalo = intervalo
        self.espera_error = espera_error
        self.finalizacion_transaccional = finalizacion_transaccional

        self.detencion = threading.Event()
        self.hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        """Recupera tareas e inicia el procesamiento."""

        if self.hilo is not None:
            raise RuntimeError("Worker ya iniciado")

        recuperadas = self.almacen.recuperar_interrumpidas()

        logger.info(
            "Notas recuperadas después del reinicio: %s",
            recuperadas,
        )

        self.hilo = threading.Thread(
            target=self._ejecutar,
            name="pia3-persistent-worker",
            daemon=True,
        )

        self.hilo.start()
        logger.info("Worker persistente iniciado")

    def _ejecutar(self) -> None:
        """Consulta SQLite y procesa notas pendientes."""

        while not self.detencion.is_set():
            try:
                nota = self.almacen.reclamar_siguiente()

                if nota is None:
                    self.detencion.wait(self.intervalo)
                    continue

                logger.info(
                    "Procesando nota persistida: %s",
                    nota.id,
                )

                try:
                    resultado = self.procesar(nota)

                    if resultado:
                        if self.finalizacion_transaccional:
                            resultado = (
                                self.almacen.obtener_estado(nota.id)
                                == "completada"
                            )
                        else:
                            resultado = self.almacen.completar(
                                nota.id
                            )

                except Exception:
                    logger.exception(
                        "Error procesando nota %s",
                        nota.id,
                    )
                    resultado = False

                if resultado:
                    logger.info(
                        "Nota %s completada",
                        nota.id,
                    )
                else:
                    if (
                        self.almacen.obtener_estado(nota.id)
                        == "procesando"
                    ):
                        self.almacen.reintentar(
                            nota.id,
                            "Procesamiento o finalización fallida",
                        )

                    logger.warning(
                        "Nota %s requiere revisión o reintento",
                        nota.id,
                    )
                    self.detencion.wait(self.espera_error)

            except Exception:
                logger.exception("Error en worker persistente")
                self.detencion.wait(self.espera_error)

    def detener(self, timeout: float = 5.0) -> bool:
        """Solicita detener el worker controladamente."""

        self.detencion.set()

        if self.hilo is None:
            return True

        self.hilo.join(timeout=timeout)
        return not self.hilo.is_alive()
