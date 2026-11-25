"""Clasifica partes confirmados en un hilo independiente."""

import json
import logging
import threading
from collections.abc import Callable
from hashlib import sha256

from planta_contrato import FilaParte
from pydantic import TypeAdapter

from sistema.part_classification import clasificar_parte
from sistema.part_classification_store import AlmacenClasificacionB1


_FILAS = TypeAdapter(list[FilaParte])
logger = logging.getLogger(__name__)


class WorkerClasificacionB1:
    def __init__(self, almacen: AlmacenClasificacionB1, catalogo: Callable[[], dict]):
        self.almacen = almacen
        self.catalogo = catalogo
        self.detencion = threading.Event()
        self.hilo: threading.Thread | None = None

    def procesar_siguiente(self) -> bool:
        trabajo = self.almacen.reclamar()
        if trabajo is None:
            return False
        try:
            filas = _FILAS.validate_json(trabajo.filas_json)
            catalogo = self.catalogo()
            if not isinstance(catalogo, dict) or not isinstance(catalogo.get("sensores"), list):
                raise ValueError("Catálogo de sensores no disponible para B1")
            decisiones = clasificar_parte(filas, catalogo)
            huella = sha256(json.dumps(catalogo, sort_keys=True).encode()).hexdigest()
            return self.almacen.completar(trabajo, decisiones, huella_catalogo=huella)
        except Exception as error:
            logger.warning("Fallo B1 en %s, versión %s, intento %s",
                           trabajo.nota_id, trabajo.version, trabajo.intento, exc_info=True)
            return self.almacen.fallar(trabajo, error)

    def iniciar(self) -> None:
        if self.hilo is not None:
            raise RuntimeError("Worker de clasificación B1 ya iniciado")
        self.hilo = threading.Thread(target=self._ejecutar, name="pia3-clasificacion-b1", daemon=True)
        self.hilo.start()

    def _ejecutar(self) -> None:
        while not self.detencion.is_set():
            try:
                if not self.procesar_siguiente():
                    self.detencion.wait(0.5)
            except Exception:
                logger.exception("Error al acceder a los trabajos B1")
                self.detencion.wait(2)

    def detener(self) -> bool:
        self.detencion.set()
        if self.hilo is None:
            return True
        self.hilo.join(timeout=5)
        return not self.hilo.is_alive()
