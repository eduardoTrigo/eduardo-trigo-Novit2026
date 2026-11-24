
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
    assert almacen.obtener_transcripcion(nota.id) is None
    assert outbox.pendientes() == 0


def test_rollback_si_falla_outbox(tmp_path):
    almacen, outbox, nota = preparar(tmp_path)

    condiciones = [
        CondicionInsegura("S3", "Humo o fuego")
    ]
    eventos, tablet = crear_mensajes_seguridad(
        nota, condiciones
    )

    # Fallar en el aviso, cuando el evento ya se insertó.
    with sqlite3.connect(almacen.ruta) as db:
        db.execute("""
            CREATE TRIGGER bloquear_outbox
            BEFORE INSERT ON outbox
            WHEN NEW.topico = 'sistema/tablet'
            BEGIN
                SELECT RAISE(ABORT, 'Error simulado');
            END
        """)

    with pytest.raises(sqlite3.IntegrityError):
        completar_con_alertas(
            almacen, nota.id, eventos, tablet, transcripcion="Hay humo"
        )

    assert almacen.obtener_estado(nota.id) == "procesando"
    assert almacen.obtener_transcripcion(nota.id) is None
    assert outbox.pendientes() == 0


def test_rollback_si_no_se_actualiza_la_nota(tmp_path):
    almacen, outbox, nota = preparar(tmp_path)
    eventos, tablet = crear_mensajes_seguridad(
        nota, [CondicionInsegura("S3", "Humo o fuego")]
    )
    with sqlite3.connect(almacen.ruta) as db:
        db.execute("""
            CREATE TRIGGER omitir_cierre
            BEFORE UPDATE OF estado ON notas
            BEGIN
                SELECT RAISE(IGNORE);
            END
        """)

    assert completar_con_alertas(
        almacen, nota.id, eventos, tablet, transcripcion="Hay humo"
    ) is False

    assert almacen.obtener_estado(nota.id) == "procesando"
    assert almacen.obtener_transcripcion(nota.id) is None
    assert outbox.pendientes() == 0
