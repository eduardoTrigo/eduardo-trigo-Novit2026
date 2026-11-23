
"""Pruebas de transacciones entre notas y outbox."""

import sqlite3

import pytest

from sistema.note_store import AlmacenNotas
from sistema.security_outbox import OutboxSeguridad
from sistema.security_transaction import completar_con_alertas
from sistema.security_messages import crear_mensajes_seguridad
from sistema.security_detector import CondicionInsegura
from test_security_messages import crear_nota


def preparar(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()

    return almacen, outbox, nota


def test_guardar_alerta_y_completar(tmp_path):
    almacen, outbox, nota = preparar(tmp_path)

    condiciones = [
        CondicionInsegura("S3", "Humo o fuego")
    ]

    eventos, tablet = crear_mensajes_seguridad(
        nota, condiciones
    )

    assert completar_con_alertas(
        almacen, nota.id, eventos, tablet
    ) is True

    assert almacen.obtener_estado(nota.id) == "completada"
    assert outbox.pendientes() == 2


def test_completar_sin_riesgos(tmp_path):
    almacen, outbox, nota = preparar(tmp_path)

    assert completar_con_alertas(
        almacen, nota.id, [], None
    ) is True

    assert almacen.obtener_estado(nota.id) == "completada"
    assert outbox.pendientes() == 0


def test_rollback_si_falla_outbox(tmp_path):
    almacen, outbox, nota = preparar(tmp_path)

    condiciones = [
        CondicionInsegura("S3", "Humo o fuego")
    ]
    eventos, tablet = crear_mensajes_seguridad(
        nota, condiciones
    )

    # Provocar un fallo de inserción en la outbox.
    with sqlite3.connect(almacen.ruta) as db:
        db.execute("""
            CREATE TRIGGER bloquear_outbox
            BEFORE INSERT ON outbox
            BEGIN
                SELECT RAISE(ABORT, 'Error simulado');
            END
        """)

    with pytest.raises(sqlite3.IntegrityError):
        completar_con_alertas(
            almacen, nota.id, eventos, tablet
        )

    assert almacen.obtener_estado(nota.id) == "procesando"
    assert outbox.pendientes() == 0
