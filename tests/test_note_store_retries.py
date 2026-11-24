
"""Pruebas del control de reintentos de notas."""

import sqlite3

from sistema.note_store import AlmacenNotas
from test_security_messages import crear_nota


def adelantar_reintento(ruta, nota_id):
    """Simula que ya transcurrió la espera."""
    with sqlite3.connect(ruta) as db:
        db.execute(
            """
            UPDATE notas
            SET proximo_intento = 0
            WHERE id = ?
            """,
            (nota_id,),
        )


def test_primer_fallo_programa_espera(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    almacen = AlmacenNotas(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()
    almacen.reintentar(nota.id, "Error STT")

    # Todavía no corresponde volver a intentarlo.
    assert almacen.reclamar_siguiente() is None
    assert almacen.pendientes() == 1
    assert almacen.agotadas() == 0


def test_reintento_despues_de_espera(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    almacen = AlmacenNotas(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()
    almacen.reintentar(nota.id, "Error temporal")

    adelantar_reintento(ruta, nota.id)

    recuperada = almacen.reclamar_siguiente()

    assert recuperada is not None
    assert recuperada.id == nota.id


def test_detener_despues_de_tres_intentos(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    almacen = AlmacenNotas(ruta)
    nota = crear_nota()

    almacen.guardar(nota)

    for _ in range(3):
        recibida = almacen.reclamar_siguiente()
        assert recibida is not None

        almacen.reintentar(nota.id, "Fallo permanente")
        adelantar_reintento(ruta, nota.id)

    # Ya se agotaron los tres intentos.
    assert almacen.reclamar_siguiente() is None
    assert almacen.agotadas() == 1
    assert almacen.pendientes() == 1


def test_migracion_conserva_notas_existentes(tmp_path):
    ruta = tmp_path / "notas.sqlite3"
    nota = crear_nota()

    # Crear esquema anterior, sin proximo_intento.
    with sqlite3.connect(ruta) as db:
        db.execute("""
            CREATE TABLE notas (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                estado TEXT NOT NULL,
                intentos INTEGER NOT NULL DEFAULT 0,
                ultimo_error TEXT,
                actualizado TEXT NOT NULL
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute(
            """
            INSERT INTO notas (id, payload, estado)
            VALUES (?, ?, 'completada')
            """,
            (nota.id, nota.model_dump_json()),
        )

    AlmacenNotas(ruta)
    almacen = AlmacenNotas(ruta)
    assert almacen.obtener_estado(nota.id) == "completada"
    assert almacen.obtener_transcripcion(nota.id) is None

    with sqlite3.connect(ruta) as db:
        columnas = {
            fila[1]: fila
            for fila in db.execute("PRAGMA table_info(notas)")
        }
        payload, transcripcion = db.execute(
            "SELECT payload, transcripcion FROM notas WHERE id = ?", (nota.id,)
        ).fetchone()

    assert "proximo_intento" in columnas
    assert columnas["transcripcion"][3] == 0
    assert type(nota).model_validate_json(payload) == nota
    assert transcripcion is None
