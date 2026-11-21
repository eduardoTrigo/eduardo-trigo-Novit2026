
"""Worker independiente para procesar notas de PIA-3."""

import logging
import queue
import threading
from collections.abc import Callable

from planta_contrato import NotaRecorrida

logger = logging.getLogger(__name__)


class WorkerSeguridad:
    """Procesa notas fuera del hilo que recibe MQTT."""

    def __init__(
        self,
        procesar: Callable[[NotaRecorrida], bool],
        capacidad: int = 100,
    ):
        if capacidad < 1:
            raise ValueError("La capacidad debe ser positiva")

        self.procesar = procesar

        self.cola: queue.Queue[NotaRecorrida | None] = (
            queue.Queue(maxsize=capacidad)
        )

        self.hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        """Inicia el hilo de procesamiento."""

        if self.hilo is not None:
            raise RuntimeError("Worker ya iniciado")

        self.hilo = threading.Thread(
            target=self._ejecutar,
            name="pia3-security-worker",
            daemon=True,
        )

        self.hilo.start()
        logger.info("Worker de seguridad iniciado")

    def encolar(self, nota: NotaRecorrida) -> bool:
        """Agrega una nota sin bloquear la recepción MQTT."""

        if self.hilo is None or not self.hilo.is_alive():
            logger.error("Worker no disponible")
            return False

        try:
            self.cola.put_nowait(nota)

            logger.info(
                "Nota %s encolada",
                nota.id,
            )
            return True

        except queue.Full:
            logger.error(
                "Cola de seguridad llena. Nota %s no aceptada",
                nota.id,
            )
            return False

    def _ejecutar(self) -> None:
        """Consume las notas pendientes."""

        while True:
            nota = self.cola.get()

            try:
                if nota is None:
                    return

                try:
                    resultado = self.procesar(nota)

                    if resultado:
                        logger.info(
                            "Nota %s procesada",
                            nota.id,
                        )
                    else:
                        logger.error(
                            "No se completó la nota %s",
                            nota.id,
                        )

                except Exception:
                    logger.exception(
                        "Error procesando nota %s",
                        nota.id,
                    )

            finally:
                self.cola.task_done()

    def detener(self, timeout: float = 5.0) -> bool:
        """Solicita detener el worker tras las tareas pendientes."""

        if self.hilo is None:
            return True

        if not self.hilo.is_alive():
            return True

        try:
            self.cola.put(None, timeout=timeout)
        except queue.Full:
            logger.error("No se pudo solicitar la detención")
            return False

        self.hilo.join(timeout=timeout)

        return not self.hilo.is_alive()
