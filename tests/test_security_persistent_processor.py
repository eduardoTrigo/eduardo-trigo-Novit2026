
"""Pruebas del procesador transaccional de seguridad."""

from unittest.mock import MagicMock, patch

from sistema.note_store import AlmacenNotas
from sistema.security_outbox import OutboxSeguridad
from sistema.security_detector import CondicionInsegura
from sistema.security_pipeline import ResultadoAnalisis
from sistema.security_persistent_processor import (
    crear_procesador_persistente,
)
from test_security_messages import crear_nota


def test_procesador_guarda_alertas(tmp_path):
    ruta = tmp_path / "seguridad.sqlite3"
    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    nota = crear_nota()

    almacen.guardar(nota)
    almacen.reclamar_siguiente()

    resultado = ResultadoAnalisis(
        texto="Hay humo en la bomba",
        condiciones=[
            CondicionInsegura("S3", "Humo o fuego")
        ],
    )

    motor = MagicMock()

    with patch(
        "sistema.security_persistent_processor.analizar_nota",
        return_value=resultado,
    ):
        procesar = crear_procesador_persistente(
            almacen, motor
        )

        assert procesar(nota) is True

    assert almacen.obtener_estado(nota.id) == "completada"
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
