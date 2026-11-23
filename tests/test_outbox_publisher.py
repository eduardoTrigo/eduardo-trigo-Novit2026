
"""Pruebas del publicador persistente MQTT."""

from unittest.mock import MagicMock

import paho.mqtt.client as mqtt

from sistema.security_outbox import OutboxSeguridad
from sistema.outbox_publisher import publicar_siguiente


def preparar(tmp_path, conectado=True):
    outbox = OutboxSeguridad(
        tmp_path / "seguridad.sqlite3"
    )

    outbox.guardar(
        "SEG-TEST-S3",
        "sistema/eventos",
        '{"id":"SEG-TEST-S3"}',
    )

    cliente = MagicMock()
    cliente.conectado.is_set.return_value = conectado

    resultado = cliente.publish.return_value
    resultado.rc = mqtt.MQTT_ERR_SUCCESS
    resultado.is_published.return_value = True

    return outbox, cliente


def test_publicar_y_confirmar(tmp_path):
    outbox, cliente = preparar(tmp_path)

    assert publicar_siguiente(outbox, cliente) is True

    cliente.publish.assert_called_once_with(
        topic="sistema/eventos",
        payload='{"id":"SEG-TEST-S3"}',
        qos=1,
    )

    cliente.publish.return_value.wait_for_publish.assert_called_once()
    assert outbox.pendientes() == 0
    assert outbox.reclamar() is None


def test_no_publicar_desconectado(tmp_path):
    outbox, cliente = preparar(
        tmp_path,
        conectado=False,
    )

    assert publicar_siguiente(outbox, cliente) is False
    assert outbox.pendientes() == 1

    cliente.publish.assert_not_called()


def test_reintentar_sin_confirmacion(tmp_path):
    outbox, cliente = preparar(tmp_path)

    cliente.publish.return_value.is_published.return_value = False

    assert publicar_siguiente(
        outbox,
        cliente,
        timeout=0.01,
    ) is False

    assert outbox.pendientes() == 1


def test_reintentar_si_mqtt_rechaza(tmp_path):
    outbox, cliente = preparar(tmp_path)

    cliente.publish.return_value.rc = mqtt.MQTT_ERR_NO_CONN

    assert publicar_siguiente(outbox, cliente) is False
    assert outbox.pendientes() == 1
