
"""Pruebas de reintentos controlados de la outbox."""

import sqlite3

from sistema.security_outbox import OutboxSeguridad


def adelantar_reintento(ruta, identificador):
    """Simula que ya transcurrió el tiempo de espera."""

    with sqlite3.connect(ruta) as db:
        db.execute(
            """
            UPDATE outbox
            SET proximo_intento = 0
            WHERE id = ?
            """,
            (identificador,),
        )


def test_primer_fallo_programa_espera(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    outbox = OutboxSeguridad(ruta)

    outbox.guardar(
        "SEG-001",
        "sistema/eventos",
        "{}",
    )

    mensaje = outbox.reclamar()
    assert mensaje["id"] == "SEG-001"

    assert outbox.fallar(
        "SEG-001",
        "Broker desconectado",
    ) is True

    assert outbox.reclamar() is None
    assert outbox.pendientes() == 1
    assert outbox.agotadas() == 0


def test_reintento_despues_de_espera(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    outbox = OutboxSeguridad(ruta)

    outbox.guardar(
        "SEG-002",
        "sistema/eventos",
        "{}",
    )

    outbox.reclamar()
    outbox.fallar("SEG-002", "Sin confirmacion")

    adelantar_reintento(ruta, "SEG-002")

    mensaje = outbox.reclamar()

    assert mensaje is not None
    assert mensaje["id"] == "SEG-002"
    assert outbox.confirmar("SEG-002") is True
    assert outbox.pendientes() == 0


def test_agotar_tres_intentos(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    outbox = OutboxSeguridad(ruta)

    outbox.guardar(
        "SEG-003",
        "sistema/eventos",
        "{}",
    )

    for _ in range(3):
        mensaje = outbox.reclamar()
        assert mensaje is not None

        assert outbox.fallar(
            "SEG-003",
            "Conexion perdida",
        ) is True

        adelantar_reintento(ruta, "SEG-003")

    assert outbox.reclamar() is None
    assert outbox.agotadas() == 1
    assert outbox.pendientes() == 1


def test_reactivar_alerta_agotada(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    outbox = OutboxSeguridad(ruta)

    outbox.guardar(
        "SEG-004",
        "sistema/eventos",
        "{}",
    )

    for _ in range(3):
        outbox.reclamar()
        outbox.fallar("SEG-004", "Sin enlace")
        adelantar_reintento(ruta, "SEG-004")

    assert outbox.agotadas() == 1

    assert outbox.reactivar("SEG-004") is True

    assert outbox.agotadas() == 0

    mensaje = outbox.reclamar()
    assert mensaje is not None
    assert mensaje["id"] == "SEG-004"


def test_migracion_conserva_alertas(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"

    # Crear esquema anterior sin proximo_intento.
    with sqlite3.connect(ruta) as db:
        db.execute("""
            CREATE TABLE outbox (
                id TEXT PRIMARY KEY,
                topico TEXT NOT NULL,
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
            INSERT INTO outbox
                (id, topico, payload, estado)
            VALUES (?, ?, ?, 'pendiente')
            """,
            (
                "SEG-005",
                "sistema/eventos",
                "{}",
            ),
        )

    outbox = OutboxSeguridad(ruta)

    assert outbox.pendientes() == 1

    with sqlite3.connect(ruta) as db:
        columnas = {
            fila[1]
            for fila in db.execute(
                "PRAGMA table_info(outbox)"
            )
        }

    assert "proximo_intento" in columnas

    mensaje = outbox.reclamar()
    assert mensaje is not None
    assert mensaje["id"] == "SEG-005"
