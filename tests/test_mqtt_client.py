
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


def test_mqtt_entrega_nota_al_router(monkeypatch):
    """El callback MQTT entrega una nota válida al router."""

    from unittest.mock import MagicMock, patch

    from sistema.mqtt_client import conectar_mqtt
    from test_security_messages import crear_nota
    import sistema.mqtt_client as modulo_mqtt

    cliente = MagicMock()

    # No establecer conexiones reales en esta prueba.
    monkeypatch.setattr(
        modulo_mqtt.mqtt,
        "Client",
        MagicMock(return_value=cliente),
    )

    encolar = MagicMock(return_value=True)

    conectar_mqtt(encolar_nota=encolar)

    mensaje = MagicMock()
    mensaje.topic = Topicos.RECORRIDAS
    mensaje.payload = (
        crear_nota().model_dump_json().encode("utf-8")
    )

    # Ejecutar el callback registrado.
    cliente.on_message(cliente, None, mensaje)

    encolar.assert_called_once()

    nota = encolar.call_args.args[0]

    assert nota.id == "NOTA-00001"


def test_mqtt_respuestas_estrictas_y_resuscripcion(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import call

    from planta_contrato import RespuestaOperador
    from sistema.mqtt_client import conectar_mqtt
    from test_security_messages import crear_nota

    cliente = MagicMock()
    cliente.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    monkeypatch.setattr(mqtt, "Client", MagicMock(return_value=cliente))
    guardar_respuesta = MagicMock(return_value=True)
    guardar_nota = MagicMock(return_value=True)
    conectar_mqtt(encolar_nota=guardar_nota, encolar_respuesta=guardar_respuesta)

    cliente.on_connect(cliente, None, None, 0, None)
    cliente.on_disconnect(cliente, None, None, 0, None)
    assert not cliente.conectado.is_set()
    cliente.on_connect(cliente, None, None, 0, None)
    assert cliente.subscribe.call_args_list == [
        call(Topicos.RECORRIDAS), call(Topicos.RESPUESTAS, qos=1),
    ] * 2

    nota = crear_nota()
    r = RespuestaOperador(id="R1", ts=nota.ts, mensaje_id="PARTE-NOTA-00001-1",
                          operador=nota.operador, texto="Sí, confirmo.", audio_url=None)
    for payload in (b"no-json", r.model_copy(update={"texto": 1}).model_dump_json(
        warnings=False).encode(), r.model_dump_json().replace('"id":', '"extra":0,"id":').encode()):
        cliente.on_message(cliente, None, SimpleNamespace(topic=Topicos.RESPUESTAS, payload=payload))
    guardar_respuesta.assert_not_called()
    cliente.on_message(cliente, None, SimpleNamespace(topic=Topicos.RESPUESTAS,
                                                      payload=r.model_dump_json().encode()))
    guardar_respuesta.assert_called_once_with(r)
    guardar_nota.assert_not_called()
