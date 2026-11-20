
"""Pruebas unitarias del cliente MQTT de PIA-3."""

import threading
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt

from planta_contrato import EstadoSistema, Topicos
from sistema.mqtt_client import publicar_estado


def test_publicar_heartbeat_conexion_activa():
    """Verifica que se publique un EstadoSistema válido."""

    cliente = MagicMock()
    cliente.conectado = threading.Event()
    cliente.conectado.set()

    cliente.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS

    publicar_estado(cliente)

    # Comprobar que se llamó exactamente una vez.
    cliente.publish.assert_called_once()

    argumentos = cliente.publish.call_args.kwargs

    # Comprobar el tópico oficial.
    assert argumentos["topic"] == Topicos.ESTADO

    # Validar el JSON enviado contra Pydantic.
    estado = EstadoSistema.model_validate_json(
        argumentos["payload"]
    )

    assert estado.sistema == "Eduardo-Trigo"
    assert estado.enlace_ok is True
    assert estado.pendientes_sincronizar == 0

    # Novit exige calidad de servicio QoS 1.
    assert argumentos["qos"] == 1


def test_no_publicar_sin_conexion():
    """No debe publicar si MQTT está desconectado."""

    cliente = MagicMock()
    cliente.conectado = threading.Event()

    publicar_estado(cliente)

    cliente.publish.assert_not_called()
