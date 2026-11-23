
"""Pruebas de recepción y encolado de notas MQTT."""

from unittest.mock import MagicMock

from planta_contrato import Topicos

from sistema.nota_router import recibir_nota_mqtt
from test_security_messages import crear_nota


def payload_valido():
    return crear_nota().model_dump_json().encode("utf-8")


def test_encolar_nota_valida():
    encolar = MagicMock(return_value=True)

    resultado = recibir_nota_mqtt(
        Topicos.RECORRIDAS,
        payload_valido(),
        encolar,
    )

    assert resultado is True
    encolar.assert_called_once()

    nota_recibida = encolar.call_args.args[0]
    assert nota_recibida.id == "NOTA-00001"


def test_rechazar_json_invalido():
    encolar = MagicMock()

    resultado = recibir_nota_mqtt(
        Topicos.RECORRIDAS,
        b"{json incorrecto",
        encolar,
    )

    assert resultado is False
    encolar.assert_not_called()


def test_ignorar_otro_topico():
    encolar = MagicMock()

    resultado = recibir_nota_mqtt(
        Topicos.ESTADO,
        payload_valido(),
        encolar,
    )

    assert resultado is False
    encolar.assert_not_called()


def test_rechazar_nota_sin_audio():
    encolar = MagicMock()

    nota = crear_nota().model_copy(
        update={"audio_url": None}
    )

    resultado = recibir_nota_mqtt(
        Topicos.RECORRIDAS,
        nota.model_dump_json().encode("utf-8"),
        encolar,
    )

    assert resultado is False
    encolar.assert_not_called()


def test_rechazar_cola_llena():
    encolar = MagicMock(return_value=False)

    resultado = recibir_nota_mqtt(
        Topicos.RECORRIDAS,
        payload_valido(),
        encolar,
    )

    assert resultado is False
    encolar.assert_called_once()
