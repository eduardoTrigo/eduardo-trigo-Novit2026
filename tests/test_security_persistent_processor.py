
"""Pruebas del procesador transaccional de seguridad."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from planta_contrato import Topicos

from sistema.note_store import AlmacenNotas
from sistema.security_outbox import OutboxSeguridad
from sistema.security_detector import CondicionInsegura
from sistema.security_messages import crear_mensajes_seguridad
from sistema.security_pipeline import ResultadoAnalisis
from sistema.security_persistent_processor import (
    crear_procesador_persistente,
)
from test_security_messages import crear_nota
from test_transcription_service import crear_wav_prueba


@pytest.mark.parametrize(
    "texto,codigo,descripcion",
    [
        ("Hay un chorro de agua a presión", "S1", "Proyección de agua a presión"),
        ("Se siente olor a gas", "S2", "Olor a gas"),
        ("Sale humo de la bomba", "S3", "Humo o fuego"),
        ("Hay un operador herido", "S4", "Persona lesionada"),
    ],
)
def test_procesador_guarda_alertas_y_transcripcion(tmp_path, texto, codigo, descripcion):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()

    audio = crear_wav_prueba()
    motor = MagicMock()
    motor.transcribir.return_value = texto
    procesar = crear_procesador_persistente(almacen, motor)

    with patch("sistema.security_pipeline.descargar_audio", return_value=audio) as descarga:
        assert procesar(nota) is True

        # Consultar el texto guardado desde otra instancia no repite el pipeline.
        restaurado = AlmacenNotas(ruta)
        assert restaurado.obtener_transcripcion(nota.id) == texto
        assert restaurado.reclamar_siguiente() is None

    descarga.assert_called_once_with(nota.audio_url)
    motor.transcribir.assert_called_once_with(audio)

    assert almacen.obtener_estado(nota.id) == "completada"
    assert outbox.pendientes() == 2
    eventos, tablet = crear_mensajes_seguridad(
        nota, [CondicionInsegura(codigo, descripcion)]
    )
    with sqlite3.connect(ruta) as db:
        publicaciones = db.execute(
            "SELECT id, topico, payload FROM outbox ORDER BY rowid"
        ).fetchall()
    assert publicaciones == [
        (f"SEG-{nota.id}-{codigo}", Topicos.EVENTOS, eventos[0].model_dump_json()),
        (f"TABLET-SEG-{nota.id}", Topicos.TABLET, tablet.model_dump_json()),
    ]


def test_reintento_antes_del_commit_puede_repetir_stt(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()
    assert almacen.guardar(nota)
    assert almacen.reclamar_siguiente() == nota
    audio = crear_wav_prueba()
    motor = MagicMock()
    motor.transcribir.return_value = "Sale humo de la bomba"
    procesar = crear_procesador_persistente(almacen, motor)

    with sqlite3.connect(ruta) as db:
        db.execute("""
            CREATE TRIGGER fallar_cierre
            AFTER UPDATE OF estado ON notas
            WHEN NEW.estado = 'completada'
            BEGIN
                SELECT RAISE(ABORT, 'Fallo antes del commit');
            END
        """)

    with patch("sistema.security_pipeline.descargar_audio", return_value=audio) as descarga:
        assert procesar(nota) is False
        assert almacen.obtener_estado(nota.id) == "procesando"
        assert almacen.obtener_transcripcion(nota.id) is None
        assert outbox.pendientes() == 0

        with sqlite3.connect(ruta) as db:
            assert db.execute(
                "SELECT transcripcion FROM notas WHERE id = ?", (nota.id,)
            ).fetchone() == (None,)
            db.execute("DROP TRIGGER fallar_cierre")
        assert almacen.recuperar_interrumpidas() == 1
        assert almacen.reclamar_siguiente() == nota
        assert procesar(nota) is True

    assert descarga.call_count == motor.transcribir.call_count == 2
    assert almacen.obtener_transcripcion(nota.id) == motor.transcribir.return_value
    assert outbox.pendientes() == 2


def test_procesador_sin_riesgos(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()

    resultado = ResultadoAnalisis(
        texto="Todo funciona normalmente",
        condiciones=[],
    )

    with patch(
        "sistema.security_persistent_processor.analizar_nota",
        return_value=resultado,
    ):
        procesar = crear_procesador_persistente(
            almacen, MagicMock()
        )

        assert procesar(nota) is True

    assert almacen.obtener_estado(nota.id) == "completada"
    assert almacen.obtener_transcripcion(nota.id) == resultado.texto
    assert outbox.pendientes() == 0


def test_procesador_no_completa_si_falla_analisis(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()

    with patch(
        "sistema.security_persistent_processor.analizar_nota",
        side_effect=RuntimeError("Fallo STT"),
    ):
        procesar = crear_procesador_persistente(
            almacen, MagicMock()
        )

        assert procesar(nota) is False

    assert almacen.obtener_estado(nota.id) == "procesando"
    assert outbox.pendientes() == 0
