"""Consultas de solo lectura al simulador existente, habilitadas explícitamente."""

import os
from datetime import datetime, timedelta

import httpx
import pytest

from sistema.plant_tools import CatalogoToolsPlanta


pytestmark = pytest.mark.skipif(
    os.getenv("PIA3_TOOLS_INTEGRATION") != "1",
    reason="Requiere un simulador local operativo y PIA3_TOOLS_INTEGRATION=1",
)


def test_consultas_reales_y_recurso_oficial(capsys):
    tools = CatalogoToolsPlanta("http://127.0.0.1:8100")
    equipos = tools.ejecutar("consultar_equipos", {"tag": "B-102"})
    assert equipos["items"][0]["manometro_local"]["unidad"] == "psi"
    assert "la dos" in equipos["items"][0]["nombres"]

    # El reloj simulado solo ubica el intervalo de la prueba.
    with httpx.Client(timeout=2.0, trust_env=False) as cliente:
        respuesta = cliente.get("http://127.0.0.1:8100/api/estado")
        respuesta.raise_for_status()
        hasta = datetime.fromisoformat(respuesta.json()["ts"])
    historian = tools.ejecutar("consultar_historial_sensores", {
        "tag": "B-102", "variable": "presion_descarga",
        "desde": hasta - timedelta(hours=1), "hasta": hasta, "limite": 3,
    })
    assert historian["unidad"] == "kg/cm2" and historian["total"] > 0
    assert len(historian["items"]) <= 3 and historian["truncado"] == (historian["total"] > 3)

    ordenes = tools.ejecutar("consultar_historial_ordenes", {"equipo": "B-102", "limite": 100})
    assert ordenes["total"] > 0 and all(orden["equipo"] == "B-102" for orden in ordenes["items"])
    origenes = {orden["origen"] for orden in ordenes["items"]}
    assert "archivo" in origenes and origenes <= {"archivo", "simulador"}

    inventario = tools.ejecutar("consultar_inventario", {"limite": 3})
    stock = tools.ejecutar("consultar_stock", {"codigo": "TX-ROD-CIG", "almacen": "yacimiento"})
    materiales = tools.ejecutar("consultar_lista_materiales", {"equipo": "F-201A"})
    tareas = tools.ejecutar("consultar_catalogo_tareas", {"codigo": "T-F-CAR"})
    assert inventario["total"] == 11 and inventario["origen"] == stock["origen"] == "referencia"
    assert stock["stock"] == {"yacimiento": 0}
    assert [m["codigo"] for m in materiales["items"]] == ["FC-CART-25", "FC-JUNTA-TAPA", "INS-TRANS-P"]
    assert tareas["total"] == 2
    with capsys.disabled():
        print(f"\nB-102: {historian['total']} muestras y {ordenes['total']} órdenes con orígenes {sorted(origenes)}. "
              f"Referencia: {inventario['total']} materiales y stock yacimiento {stock['stock']['yacimiento']} para TX-ROD-CIG.")
