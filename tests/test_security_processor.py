
"""Pruebas del orquestador de seguridad."""

from unittest.mock import MagicMock, patch

from sistema.security_processor import procesar_nota_seguridad
from sistema.security_pipeline import ResultadoAnalisis
from sistema.security_detector import CondicionInsegura

from test_security_messages import crear_nota


def test_procesar_nota_con_alerta():
    nota = crear_nota()
    motor = MagicMock()
    cliente = MagicMock()

    analisis = ResultadoAnalisis(
        texto="Hay humo en la bomba",
        condiciones=[
            CondicionInsegura("S3", "Humo o fuego")
        ],
    )

    with patch(
        "sistema.security_processor.analizar_nota",
        return_value=analisis,
    ), patch(
        "sistema.security_processor.publicar_alertas_seguridad",
        return_value=True,
    ) as publicar:

        resultado = procesar_nota_seguridad(
            nota, motor, cliente
        )

    assert resultado is True
    publicar.assert_called_once()

    eventos, tablet = publicar.call_args.args[1:]

    assert len(eventos) == 1
    assert tablet is not None


def test_procesar_nota_sin_riesgos():
    nota = crear_nota()

    analisis = ResultadoAnalisis(
        texto="Todo funciona normalmente",
        condiciones=[],
    )

    with patch(
        "sistema.security_processor.analizar_nota",
        return_value=analisis,
    ), patch(
        "sistema.security_processor.publicar_alertas_seguridad",
    ) as publicar:

        resultado = procesar_nota_seguridad(
            nota, MagicMock(), MagicMock()
        )

    assert resultado is True
    publicar.assert_not_called()


def test_no_procesar_nota_sin_audio():
    nota = crear_nota().model_copy(
        update={"audio_url": None}
    )

    with patch(
        "sistema.security_processor.analizar_nota"
    ) as analizar:

        resultado = procesar_nota_seguridad(
            nota, MagicMock(), MagicMock()
        )

    assert resultado is False
    analizar.assert_not_called()
