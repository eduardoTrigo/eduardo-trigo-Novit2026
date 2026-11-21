
"""Pruebas iniciales del detector local de seguridad."""

import pytest

from sistema.security_detector import (
    detectar_condiciones_inseguras,
)


@pytest.mark.parametrize(
    "frase,codigo",
    [
        ("Hay un chorro de agua a presión", "S1"),
        ("Se siente olor a gas", "S2"),
        ("Sale humo de la bomba", "S3"),
        ("Hay un operador herido", "S4"),
    ],
)
def test_detectar_condicion_insegura(frase, codigo):
    condiciones = detectar_condiciones_inseguras(frase)

    assert any(
        condicion.codigo == codigo
        for condicion in condiciones
    )


def test_sin_condicion_insegura():
    texto = "La bomba está en reserva, sin novedades"

    condiciones = detectar_condiciones_inseguras(texto)

    assert condiciones == []


def test_detectar_multiples_condiciones():
    texto = "Hay humo en la sala y un operador herido"

    condiciones = detectar_condiciones_inseguras(texto)

    codigos = {condicion.codigo for condicion in condiciones}

    assert codigos == {"S3", "S4"}

# evita falsas alarmas
@pytest.mark.parametrize(
    "frase",
    [
        "No hay humo en la bomba",
        "No se siente olor a gas",
        "No hay fuego en la sala",
        "No hay un operador herido",
        "No hay un chorro de agua a presión",
    ],
)
def test_no_alertar_condiciones_negadas(frase):
    """Una condición negada no debe generar alerta."""

    condiciones = detectar_condiciones_inseguras(frase)

    assert condiciones == []


def test_alertar_solo_condicion_afirmativa():
    """Debe distinguir afirmaciones y negaciones en una nota."""

    texto = "Hay humo en la bomba, pero no hay un operador herido"

    condiciones = detectar_condiciones_inseguras(texto)

    codigos = {condicion.codigo for condicion in condiciones}

    assert codigos == {"S3"}
