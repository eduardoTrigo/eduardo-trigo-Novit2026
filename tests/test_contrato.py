
"""Pruebas de compatibilidad con el contrato oficial de Novit."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from planta_contrato import EstadoSistema


def test_estado_sistema_valido():
    """Comprueba que un latido respete el contrato oficial."""

    estado = EstadoSistema(
        ts=datetime.now(timezone.utc),
        sistema="Eduardo-Trigo",
        enlace_ok=True,
        pendientes_sincronizar=0,
    )

    assert estado.sistema == "Eduardo-Trigo"
    assert estado.enlace_ok is True
    assert estado.pendientes_sincronizar == 0


def test_estado_sistema_rechaza_campos_extra():
    """El contrato no debe aceptar campos desconocidos."""

    with pytest.raises(ValidationError):
        EstadoSistema(
            ts=datetime.now(timezone.utc),
            sistema="Eduardo-Trigo",
            enlace_ok=True,
            pendientes_sincronizar=0,
            campo_invalido="no permitido",
        )
