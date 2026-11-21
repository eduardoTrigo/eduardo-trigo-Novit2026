
"""Pruebas de mensajes oficiales de seguridad."""

from datetime import datetime, timezone

from planta_contrato import NotaRecorrida, TipoEvento

from sistema.security_detector import CondicionInsegura
from sistema.security_messages import crear_mensajes_seguridad


def crear_nota():
    return NotaRecorrida(
        id="NOTA-00001",
        ts=datetime(2026, 11, 20, 15, 30, tzinfo=timezone.utc),
        operador="Juan",
        audio_url="/media/audios/NOTA-00001.wav",
        fotos=[],
        recorrida_id="REC-00001",
    )


def test_generar_alerta_y_aviso_tablet():
    nota = crear_nota()

    condiciones = [
        CondicionInsegura("S3", "Humo o fuego")
    ]

    eventos, tablet = crear_mensajes_seguridad(
        nota, condiciones
    )

    assert len(eventos) == 1
    assert eventos[0].tipo == TipoEvento.alerta_seguridad
    assert eventos[0].nota_ids == [nota.id]
    assert eventos[0].ts == nota.ts

    assert tablet is not None
    assert tablet.espera_respuesta is False
    assert "radio" in tablet.texto.lower()
    assert "supervisor" in tablet.texto.lower()


def test_sin_condiciones_no_genera_alertas():
    eventos, tablet = crear_mensajes_seguridad(
        crear_nota(), []
    )

    assert eventos == []
    assert tablet is None


def test_identificadores_idempotentes():
    nota = crear_nota()
    condiciones = [
        CondicionInsegura("S2", "Olor a gas")
    ]

    primera = crear_mensajes_seguridad(nota, condiciones)
    segunda = crear_mensajes_seguridad(nota, condiciones)

    assert primera[0][0].id == segunda[0][0].id
    assert primera[1].id == segunda[1].id
