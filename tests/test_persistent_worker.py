
"""Pruebas del worker persistente PIA-3."""

import threading

from sistema.note_store import AlmacenNotas
from sistema.persistent_worker import WorkerPersistente
from test_security_messages import crear_nota
from sistema.security_transaction import completar_con_alertas
from sistema.security_messages import crear_mensajes_seguridad
from sistema.security_detector import CondicionInsegura
from sistema.security_outbox import OutboxSeguridad


def test_worker_procesa_nota_persistida(tmp_path):
    db = AlmacenNotas(tmp_path / "notas.sqlite3")
    nota = crear_nota()
    procesada = threading.Event()

    def procesar(nota_recibida):
        assert nota_recibida.id == nota.id
        procesada.set()
        return True

    db.guardar(nota)

    worker = WorkerPersistente(
        almacen=db,
        procesar=procesar,
        intervalo=0.05,
    )

    worker.iniciar()

    try:
        assert procesada.wait(timeout=3)
    finally:
        assert worker.detener()

    assert db.obtener_estado(nota.id) == "completada"


def test_worker_recupera_nota_interrumpida(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    nota = crear_nota()

    primera = AlmacenNotas(ruta)
    primera.guardar(nota)
    primera.reclamar_siguiente()

    # Simula un reinicio: se abre otra instancia.
    segunda = AlmacenNotas(ruta)
    procesada = threading.Event()

    def procesar(nota_recibida):
        procesada.set()
        return True

    worker = WorkerPersistente(
        almacen=segunda,
        procesar=procesar,
        intervalo=0.05,
    )

    worker.iniciar()

    try:
        assert procesada.wait(timeout=3)
    finally:
        assert worker.detener()

    assert segunda.obtener_estado(nota.id) == "completada"


def test_worker_reintenta_si_falla(tmp_path):
    db = AlmacenNotas(tmp_path / "notas.sqlite3")
    nota = crear_nota()
    intento_realizado = threading.Event()

    def procesar(nota_recibida):
        intento_realizado.set()
        return False

    db.guardar(nota)

    worker = WorkerPersistente(
        almacen=db,
        procesar=procesar,
        intervalo=0.05,
        espera_error=2.0,
    )

    worker.iniciar()

    try:
        assert intento_realizado.wait(timeout=3)
    finally:
        assert worker.detener()

    assert db.obtener_estado(nota.id) == "pendiente"


def test_worker_finalizacion_transaccional(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()
    terminado = threading.Event()

    almacen.guardar(nota)

    def procesar(nota_recibida):
        condiciones = [
            CondicionInsegura("S3", "Humo o fuego")
        ]

        eventos, tablet = crear_mensajes_seguridad(
            nota_recibida,
            condiciones,
        )

        resultado = completar_con_alertas(
            almacen,
            nota_recibida.id,
            eventos,
            tablet,
        )

        terminado.set()
        return resultado

    worker = WorkerPersistente(
        almacen=almacen,
        procesar=procesar,
        intervalo=0.05,
        finalizacion_transaccional=True,
    )

    worker.iniciar()

    try:
        assert terminado.wait(timeout=3)
    finally:
        assert worker.detener()

    assert almacen.obtener_estado(nota.id) == "completada"
    assert outbox.pendientes() == 2


def test_worker_no_completa_sin_transaccion(tmp_path):
    almacen = AlmacenNotas(tmp_path / "seguridad.sqlite3")
    nota = crear_nota()
    procesado = threading.Event()

    almacen.guardar(nota)

    def procesar(nota_recibida):
        procesado.set()
        return True

    worker = WorkerPersistente(
        almacen=almacen,
        procesar=procesar,
        intervalo=0.05,
        espera_error=2.0,
        finalizacion_transaccional=True,
    )

    worker.iniciar()

    try:
        assert procesado.wait(timeout=3)
    finally:
        assert worker.detener()

    # No hubo transacción: debe seguir pendiente.
    assert almacen.obtener_estado(nota.id) == "pendiente"
