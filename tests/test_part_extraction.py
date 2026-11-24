import httpx
import pytest
from planta_contrato import FilaParte


def definicion_planta():
    return {
        "equipos": [
            {"tag": "B-101", "tipo": "bomba", "nombres": ["la uno", "la 1"],
             "manometro_local": {"unidad": "kg/cm2"}},
            {"tag": "B-102", "tipo": "bomba", "nombres": ["la dos", "la del medio"],
             "manometro_local": {"unidad": "psi"}},
            {"tag": "B-103", "tipo": "bomba", "nombres": ["la tres", "la de reserva"],
             "manometro_local": {"unidad": "bar"}},
            {"tag": "F-201A", "tipo": "filtro", "nombres": ["el filtro A", "el primario", "el de la izquierda"]},
            {"tag": "F-201B", "tipo": "filtro", "nombres": ["el filtro B", "el secundario", "el de la derecha"]},
            {"tag": "TK-301", "tipo": "tanque", "nombres": ["el pulmón"]},
        ],
        "sensores": [
            {"tag": "F-201A", "variable": "presion_diferencial", "unidad": "kg/cm2"},
            {"tag": "F-201B", "variable": "presion_diferencial", "unidad": "kg/cm2"},
        ],
    }


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("La uno tiene presión de succión de menos cero coma dos bar", [
            ("B-101", "presion_succion", -0.2, "bar"),
        ]),
        ("La uno tiene presión de succión de menos uno con ocho bar", [
            ("B-101", "presion_succion", -1.8, "bar"),
        ]),
        ("La uno tiene presión de succión de -0,2 bar", [
            ("B-101", "presion_succion", -0.2, "bar"),
        ]),
        ("La uno tiene presión de succión de cero coma dos bar", [
            ("B-101", "presion_succion", 0.2, "bar"),
        ]),
        ("La dos está en 1640 de descarga", [
            ("B-102", "presion_descarga_local", 1640, "psi"),
        ]),
        ("La uno está en ciento quince kilos de descarga, "
         "la dos en mil seiscientas cuarenta libras de descarga y "
         "la tres en ciento diez bares de descarga", [
            ("B-101", "presion_descarga_local", 115, "kg/cm2"),
            ("B-102", "presion_descarga_local", 1640, "psi"),
            ("B-103", "presion_descarga_local", 110, "bar"),
        ]),
        ("B-101 está en 115 de descarga, el rodamiento me dio 74 grados. "
         "El de la izquierda me dio uno ocho de diferencial", [
            ("B-101", "presion_descarga_local", 115, "kg/cm2"),
            ("B-101", "temperatura_rodamiento_ir", 74, "degC"),
            ("F-201A", "presion_diferencial", 1.8, "kg/cm2"),
        ]),
        ("El pulmón está en cinco con dos metros. "
         "El primario me dio 1,8 de diferencial. El primario me dio 1.8 de diferencial", [
            ("TK-301", "nivel", 5.2, "m"),
            ("F-201A", "presion_diferencial", 1.8, "kg/cm2"),
        ]),
        ("La tres gotea por la empaquetadura", [
            ("B-103", "goteo_empaquetadura", None, None),
        ]),
        ("La tres hace un golpeteo metálico", [
            ("B-103", "ruido", None, None),
        ]),
        ("La uno está en 115 de descarga y el rodamiento me dio veintiún grados", [
            ("B-101", "presion_descarga_local", 115, "kg/cm2"),
            ("B-101", "temperatura_rodamiento_ir", 21, "degC"),
        ]),
        ("La uno está en 115 de descarga, el rodamiento me dio 79 °C", [
            ("B-101", "presion_descarga_local", 115, "kg/cm2"),
            ("B-101", "temperatura_rodamiento_ir", 79, "degC"),
        ]),
        ("La 1 está en 114 kilos de descarga. El rodamiento medio 64 grados con la pistola.", [
            ("B-101", "presion_descarga_local", 114, "kg/cm2"),
            ("B-101", "temperatura_rodamiento_ir", 64, "degC"),
        ]),
        ("El de la derecha medio 0-9 de diferencial.", [
            ("F-201B", "presion_diferencial", 0.9, "kg/cm2"),
        ]),
    ],
)
def test_extraer_filas_validas(texto, esperado):
    from sistema.part_extraction import extraer_parte

    resultado = extraer_parte(texto, definicion_planta())
    assert not resultado.pendiente
    assert [(f.tag, f.variable, f.valor, f.unidad) for f in resultado.filas] == esperado
    for fila in resultado.filas:
        assert FilaParte.model_validate_json(fila.model_dump_json()) == fila
        if fila.valor is None:
            assert fila.observacion


@pytest.mark.parametrize(
    "texto",
    [
        "La bomba está en 115 kilos de descarga",
        "El primario me dio dieciocho de diferencial",
        "La uno está en 115 de descarga. La uno está en 110 de descarga",
        "B-999 está en 115 kilos de descarga",
        "La uno está en 115 metros de descarga",
        "La uno está en 115 litros de descarga",
        "La uno está de descarga",
        "La uno no está en 115 kilos de descarga",
        "La uno no tiene presión de succión de menos cero coma dos bar",
        "La uno tiene menos de cero coma dos bar de succión",
        "La uno tiene presión de succión de menos, cero coma dos bar",
        "La uno tiene presión de succión de menos -0,2 bar",
        "La dos está en más o menos 1640 psi de descarga",
        "La uno y la dos están en marcha. El rodamiento medio 64 grados.",
    ],
)
def test_dato_incierto_queda_pendiente(texto):
    from sistema.part_extraction import extraer_parte

    resultado = extraer_parte(texto, definicion_planta())
    assert resultado.pendiente
    assert resultado.filas == []


def test_recorrida_sin_datos_no_inventa_lecturas():
    from sistema.part_extraction import extraer_parte

    resultado = extraer_parte("Recorrida sin novedad, todo normal", definicion_planta())
    assert resultado.filas == []
    assert not resultado.pendiente


def test_catalogo_solo_consulta_api_planta(monkeypatch):
    from sistema.part_context import ClientePlanta

    llamadas = []

    def obtener(url, **opciones):
        llamadas.append((url, opciones))
        return httpx.Response(200, json=definicion_planta(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", obtener)
    cliente = ClientePlanta("http://sim:8100")
    assert cliente.catalogo()["equipos"][0]["tag"] == "B-101"
    assert cliente.catalogo()["equipos"][0]["tag"] == "B-101"
    assert len(llamadas) == 1
    assert llamadas[0][0] == "http://sim:8100/api/planta"
    assert 0 < llamadas[0][1]["timeout"] <= 5
