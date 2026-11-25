"""Procesa el traspaso y el enriquecimiento B1 en hilos independientes."""

import logging
import threading
from collections.abc import Callable

from sistema.b1_event_store import AlmacenEventosB1
from sistema.b1_evidence import EnriquecedorB1

logger = logging.getLogger(__name__)


class WorkerEventosB1:
    def __init__(self, almacen: AlmacenEventosB1, enriquecedor: EnriquecedorB1):
        self.almacen = almacen
        self.enriquecedor = enriquecedor
        self.detencion = threading.Event()
        self.hilos: list[threading.Thread] = []

    def iniciar(self) -> None:
        if self.hilos:
            raise RuntimeError("Worker de eventos B1 ya iniciado")
        for nombre, procesar in (("encolado", self.almacen.encolar_siguiente),
                                 ("evidencia", self.enriquecedor.procesar_siguiente)):
            hilo = threading.Thread(
                target=self._ejecutar, args=(nombre, procesar), name=f"pia3-b1-{nombre}", daemon=True,
            )
            hilo.start()
            self.hilos.append(hilo)

    def _ejecutar(self, nombre: str, procesar: Callable[[], bool]) -> None:
        while not self.detencion.is_set():
            try:
                if not procesar():
                    self.detencion.wait(0.5)
            except Exception:
                logger.exception("Fallo de %s B1", nombre)
                self.detencion.wait(2)

    def detener(self) -> bool:
        self.detencion.set()
        for hilo in self.hilos:
            hilo.join(timeout=5)
        return all(not hilo.is_alive() for hilo in self.hilos)
