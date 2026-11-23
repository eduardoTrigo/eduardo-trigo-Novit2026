
"""Pruebas del publicador MQTT de seguridad."""

import threading
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt

from planta_contrato import (
    EventoSistema,
    MensajeTablet,
    Topicos,
)

from sistema.security_publisher import (
    publicar_alertas_seguridad,
)

from test_security_messages import crear_nota
from sistema.security_detector import CondicionInsegura
from sistema.security_messages import crear_mensajes_seguridad


def crear_cliente_simulado(conectado=True):
    cliente = MagicMock()
    cliente.conectado = threading.Event()

    if conectado:
        cliente.conectado.set()

    cliente.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    return cliente


def crear_alerta_prueba():
    nota = crear_nota()
    condiciones = [
        CondicionInsegura("S3", "Humo o fuego")
    ]

    return crear_mensajes_seguridad(nota, condiciones)


def test_publicar_alerta_y_tablet():
    cliente = crear_cliente_simulado()
    eventos, tablet = crear_alerta_prueba()

    resultado = publicar_alertas_seguridad(
        cliente, eventos, tablet
    )

    assert resultado is True
    assert cliente.publish.call_count == 2

    llamadas = cliente.publish.call_args_list

    assert llamadas[0].kwargs["topic"] == Topicos.EVENTOS
    assert llamadas[1].kwargs["topic"] == Topicos.TABLET

    for llamada in llamadas:
        assert llamada.kwargs["qos"] == 1

    EventoSistema.model_validate_json(
        llamadas[0].kwargs["payload"]
    )

    MensajeTablet.model_validate_json(
        llamadas[1].kwargs["payload"]
    )


def test_no_publicar_desconectado():
    cliente = crear_cliente_simulado(conectado=False)
    eventos, tablet = crear_alerta_prueba()

    resultado = publicar_alertas_seguridad(
        cliente, eventos, tablet
    )

    assert resultado is False
    cliente.publish.assert_not_called()
