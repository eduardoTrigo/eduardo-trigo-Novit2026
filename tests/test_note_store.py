
"""Pruebas de persistencia y recuperación de notas PIA-3."""

from sistema.note_store import AlmacenNotas
from test_security_messages import crear_nota


def test_guardar_y_evitar_duplicados(tmp_path):
    db = AlmacenNotas(tmp_path / "notas.sqlite3")
    nota = crear_nota()

    assert db.guardar(nota) is True
    assert db.guardar(nota) is False

    assert db.pendientes() == 1
    assert db.obtener_estado(nota.id) == "pendiente"


def test_recuperar_nota_tras_reinicio(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    nota = crear_nota()

    primera_instancia = AlmacenNotas(ruta)
    primera_instancia.guardar(nota)

    # Simular el cierre y reapertura del proceso.
    segunda_instancia = AlmacenNotas(ruta)

    recuperada = segunda_instancia.reclamar_siguiente()

    assert recuperada is not None
    assert recuperada.id == nota.id
    assert segunda_instancia.obtener_estado(
        nota.id
    ) == "procesando"


def test_completar_nota(tmp_path):
    db = AlmacenNotas(tmp_path / "notas.sqlite3")
    nota = crear_nota()

    db.guardar(nota)
    db.reclamar_siguiente()

    assert db.completar(nota.id) is True
    assert db.obtener_estado(nota.id) == "completada"
    assert db.reclamar_siguiente() is None


def test_recuperar_procesamiento_interrumpido(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    nota = crear_nota()

    db = AlmacenNotas(ruta)
    db.guardar(nota)
    db.reclamar_siguiente()

    # El trabajo quedó marcado como procesando.
    reiniciada = AlmacenNotas(ruta)

    assert reiniciada.recuperar_interrumpidas() == 1
    assert reiniciada.pendientes() == 1

    recuperada = reiniciada.reclamar_siguiente()

    assert recuperada.id == nota.id
    assert reiniciada.completar(nota.id) is True


def test_reintentar_fallo(tmp_path):
    db = AlmacenNotas(tmp_path / "notas.sqlite3")
    nota = crear_nota()

    db.guardar(nota)
    db.reclamar_siguiente()

    assert db.reintentar(
        nota.id,
        "Error temporal de descarga",
    ) is True

    assert db.obtener_estado(nota.id) == "pendiente"
    
    # El reintento no debe ejecutarse inmediatamente.
    assert db.reclamar_siguiente() is None

    # Simulamos que ya pasó el tiempo de espera.
    import sqlite3

    with sqlite3.connect(db.ruta) as conexion:
        conexion.execute(
            """
            UPDATE notas
            SET proximo_intento = 0
            WHERE id = ?
            """,
            (nota.id,),
        )

    # Ahora la nota vuelve a estar disponible.
    recuperada = db.reclamar_siguiente()

    assert recuperada is not None
    assert recuperada.id == nota.id
