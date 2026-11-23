
"""Pruebas del enlace entre worker, STT y seguridad."""

from unittest.mock import MagicMock, patch

from sistema.security_runtime import crear_procesador_seguridad
from test_security_messages import crear_nota


def test_runtime_procesa_nota_correctamente():
    nota = crear_nota()
    motor = MagicMock()
    cliente = MagicMock()

    with patch(
        "sistema.security_runtime.procesar_nota_seguridad",
        return_value=True,
    ) as procesador:

        ejecutar = crear_procesador_seguridad(
            motor=motor,
            cliente=cliente,
        )

        resultado = ejecutar(nota)

        assert resultado is True

        procesador.assert_called_once_with(
            nota=nota,
            motor=motor,
            cliente=cliente,
        )


def test_runtime_propaga_fallo_de_procesamiento():
    nota = crear_nota()
    motor = MagicMock()
    cliente = MagicMock()

    with patch(
        "sistema.security_runtime.procesar_nota_seguridad",
        return_value=False,
    ):
        ejecutar = crear_procesador_seguridad(
            motor=motor,
            cliente=cliente,
        )

        assert ejecutar(nota) is False
