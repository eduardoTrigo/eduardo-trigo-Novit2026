
"""Pruebas del worker de publicaciones persistentes."""

import threading
from unittest.mock import MagicMock, patch

from sistema.outbox_worker import WorkerOutbox
from sistema.security_outbox import OutboxSeguridad


def test_worker_publica_pendiente(tmp_path):
    outbox = OutboxSeguridad(tmp_path / "test.sqlite3")
    outbox.guardar("SEG-001", "sistema/eventos", "{}")

    cliente = MagicMock()
    publicado = threading.Event()

    def simular_publicacion(outbox, cliente, timeout):
        mensaje = outbox.reclamar()
        assert mensaje is not None
        outbox.confirmar(mensaje["id"])
        publicado.set()
        return True

    with patch(
        "sistema.outbox_worker.publicar_siguiente",
        side_effect=simular_publicacion,
    ):
        worker = WorkerOutbox(outbox, cliente, intervalo=0.05)
        worker.iniciar()

        try:
            assert publicado.wait(timeout=3)
        finally:
            assert worker.detener()

    assert outbox.pendientes() == 0


def test_worker_recupera_envio_interrumpido(tmp_path):
    ruta = tmp_path / "test.sqlite3"

    primera = OutboxSeguridad(ruta)
    primera.guardar("SEG-002", "sistema/eventos", "{}")
    primera.reclamar()

    segunda = OutboxSeguridad(ruta)
    cliente = MagicMock()
    publicado = threading.Event()

    def simular_publicacion(outbox, cliente, timeout):
        mensaje = outbox.reclamar()
        assert mensaje is not None
        assert mensaje["id"] == "SEG-002"
        outbox.confirmar(mensaje["id"])
        publicado.set()
        return True

    with patch(
        "sistema.outbox_worker.publicar_siguiente",
        side_effect=simular_publicacion,
    ):
        worker = WorkerOutbox(
            segunda,
            cliente,
            intervalo=0.05,
        )
        worker.iniciar()

        try:
            assert publicado.wait(timeout=3)
        finally:
            assert worker.detener()

    assert segunda.pendientes() == 0


def test_worker_sin_mensajes(tmp_path):
    outbox = OutboxSeguridad(tmp_path / "test.sqlite3")
    cliente = MagicMock()

    with patch(
        "sistema.outbox_worker.publicar_siguiente"
    ) as publicar:
        worker = WorkerOutbox(
            outbox,
            cliente,
            intervalo=0.05,
        )

        worker.iniciar()

        try:
            assert worker.detener()
        finally:
            publicar.assert_not_called()
