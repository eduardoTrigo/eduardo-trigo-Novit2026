
"""Pruebas del worker de seguridad PIA-3."""

import threading
from unittest.mock import MagicMock

from sistema.security_worker import WorkerSeguridad
from test_security_messages import crear_nota


def test_worker_procesa_nota():
    procesada = threading.Event()

    def procesar(nota):
        procesada.set()
        return True

    worker = WorkerSeguridad(procesar=procesar)
    worker.iniciar()

    try:
        assert worker.encolar(crear_nota()) is True
        assert procesada.wait(timeout=2)
    finally:
        assert worker.detener()


def test_worker_continua_tras_excepcion():
    segunda_procesada = threading.Event()
    llamadas = 0

    def procesar(nota):
        nonlocal llamadas
        llamadas += 1

        if llamadas == 1:
            raise RuntimeError("Error simulado")

        segunda_procesada.set()
        return True

    worker = WorkerSeguridad(procesar=procesar)
    worker.iniciar()

    try:
        nota1 = crear_nota()
        nota2 = crear_nota().model_copy(
            update={"id": "NOTA-00002"}
        )

        assert worker.encolar(nota1)
        assert worker.encolar(nota2)

        assert segunda_procesada.wait(timeout=2)
        assert llamadas == 2
    finally:
        assert worker.detener()


def test_worker_rechaza_cola_llena():
    procesando = threading.Event()
    liberar = threading.Event()

    def procesar(nota):
        procesando.set()
        liberar.wait(timeout=3)
        return True

    worker = WorkerSeguridad(
        procesar=procesar,
        capacidad=1,
    )
    worker.iniciar()

    try:
        nota = crear_nota()

        assert worker.encolar(nota)
        assert procesando.wait(timeout=2)

        segunda = nota.model_copy(
            update={"id": "NOTA-00002"}
        )
        tercera = nota.model_copy(
            update={"id": "NOTA-00003"}
        )

        assert worker.encolar(segunda) is True
        assert worker.encolar(tercera) is False

    finally:
        liberar.set()
        assert worker.detener()


def test_worker_no_iniciado():
    procesar = MagicMock(return_value=True)

    worker = WorkerSeguridad(procesar=procesar)

    assert worker.encolar(crear_nota()) is False
    procesar.assert_not_called()
