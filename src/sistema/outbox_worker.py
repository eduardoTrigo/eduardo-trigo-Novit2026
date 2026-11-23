
"""Worker independiente para publicar mensajes de la outbox."""

import logging
import threading

import paho.mqtt.client as mqtt

from sistema.security_outbox import OutboxSeguridad
from sistema.outbox_publisher import publicar_siguiente

logger = logging.getLogger(__name__)


class WorkerOutbox:
    """Publica mensajes persistidos en segundo plano."""

    def __init__(
        self,
        outbox: OutboxSeguridad,
        cliente: mqtt.Client,
        intervalo: float = 0.5,
        espera_error: float = 3.0,
    ):
        if intervalo <= 0 or espera_error <= 0:
            raise ValueError("Los intervalos deben ser positivos")

        self.outbox = outbox
        self.cliente = cliente
        self.intervalo = intervalo
        self.espera_error = espera_error

        self.detencion = threading.Event()
        self.hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        """Recupera publicaciones e inicia el worker."""

        if self.hilo is not None:
            raise RuntimeError("Worker outbox ya iniciado")

        recuperados = self.outbox.recuperar()

        logger.info(
            "Publicaciones recuperadas: %s",
            recuperados,
        )

        self.hilo = threading.Thread(
            target=self._ejecutar,
            name="pia3-outbox-worker",
            daemon=True,
        )

        self.hilo.start()
        logger.info("Worker outbox iniciado")

    def _ejecutar(self) -> None:
        """Procesa envíos mientras el servicio esté activo."""

        while not self.detencion.is_set():
            try:
                if self.outbox.pendientes() == 0:
                    self.detencion.wait(self.intervalo)
                    continue

                publicado = publicar_siguiente(
                    self.outbox,
                    self.cliente,
                    timeout=5.0,
                )

                if not publicado:
                    self.detencion.wait(self.espera_error)

            except Exception:
                logger.exception("Error en worker outbox")
                self.detencion.wait(self.espera_error)

    def detener(self, timeout: float = 10.0) -> bool:
        """Detiene el worker de manera controlada."""

        self.detencion.set()

        if self.hilo is None:
            return True

        self.hilo.join(timeout=timeout)
        return not self.hilo.is_alive()
