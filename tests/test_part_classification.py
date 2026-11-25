from copy import deepcopy
from math import inf, nan, nextafter

import pytest
from planta_contrato import FilaParte

from sistema.part_classification import clasificar_parte


@pytest.fixture
def catalogo():
    # Subconjunto de planta.yaml, con los límites en la unidad de cada sensor.
    return {
        "equipos": [
            {"tag": "B-101", "tipo": "bomba"},
            {"tag": "F-201A", "tipo": "filtro"},
        ],
        "sensores": [
            {"tag": "B-101", "variable": "presion_descarga", "unidad": "kg/cm2",
             "alarma_baja": 95, "alarma_alta": 125},
            {"tag": "B-101", "variable": "temperatura_aceite", "unidad": "degC",
             "alarma_alta": 80, "disparo": 90},
            {"tag": "F-201A", "variable": "presion_diferencial", "unidad": "kg/cm2",
             "alarma_baja": 0.15, "alarma_alta": 1.5},
        ],
    }


def lectura(valor=110, unidad="kg/cm2", *, variable="presion_descarga", tag="B-101"):
    return FilaParte(tag=tag, variable=variable, valor=valor, unidad=unidad)


def observacion(texto, variable="estado"):
    return FilaParte(tag="B-101", variable=variable, observacion=texto)


def decision(catalogo, *filas):
    (resultado,) = clasificar_parte(filas, catalogo)
    return resultado


@pytest.mark.parametrize(("valor", "resultado", "motivo"), [
    (110, "SinNovedad", "dentro_de_limites"),
    (125, "Escalado", "alarma_alta"),
    (95, "Escalado", "alarma_baja"),
    (nextafter(125, inf), "Escalado", "alarma_alta"),
    (nextafter(125, -inf), "SinNovedad", "dentro_de_limites"),
    (nextafter(95, inf), "SinNovedad", "dentro_de_limites"),
    (nextafter(95, -inf), "Escalado", "alarma_baja"),
])
def test_limites_inclusivos(catalogo, valor, resultado, motivo):
    d = decision(catalogo, lectura(valor))
    assert d.resultado == resultado
    assert d.evidencia[0].motivo == motivo


@pytest.mark.parametrize(("valor", "unidad", "resultado"), [
    (113 * 14.2233, " PSI ", "SinNovedad"),
    (130 * 0.980665, "bar", "Escalado"),
    (94 * 14.2233, "psi", "Escalado"),
    (110 * 0.980665, "bares", "SinNovedad"),
])
def test_presion_convertida_con_evidencia_original(catalogo, valor, unidad, resultado):
    d = decision(catalogo, lectura(valor, unidad, variable="presion_descarga_local"))
    assert d.resultado == resultado
    (e,) = d.evidencia
    assert (e.variable, e.variable_canonica) == ("presion_descarga_local", "presion_descarga")
    assert (e.valor, e.unidad) == (valor, unidad)
    assert e.unidad_canonica == "kg/cm2"
    assert e.valor_canonico == pytest.approx(valor / (0.980665 if "bar" in unidad else 14.2233))
    assert (e.alarma_baja, e.alarma_alta) == (95, 125)
    assert e.motivo in {"dentro_de_limites", "alarma_alta", "alarma_baja"}


def test_diferencial_local_y_decisiones_por_equipo(catalogo):
    filtro = lectura(1.5, variable="presion_diferencial_local", tag="F-201A")
    resultados = clasificar_parte([filtro, lectura()], catalogo)
    assert {d.equipo: d.resultado for d in resultados} == {
        "B-101": "SinNovedad", "F-201A": "Escalado",
    }
    assert next(d for d in resultados if d.equipo == "F-201A").evidencia[0].variable_canonica == (
        "presion_diferencial"
    )


def test_usa_limites_suministrados_y_conserva_disparo(catalogo):
    catalogo["sensores"][1].update(alarma_alta=61, disparo=69)
    d = decision(catalogo, lectura(70, "°C", variable="temperatura_aceite"))
    assert d.resultado == "Escalado"
    assert d.evidencia[0].disparo == 69
    assert d.evidencia[0].valor_canonico == 70


def test_anomalia_prevalece_y_conserva_otras_razones(catalogo):
    d = decision(catalogo, lectura(), lectura(83, "degC", variable="temperatura_aceite"),
                 lectura(1, "m", variable="presion_succion"))
    assert d.resultado == "Escalado"
    assert {e.motivo for e in d.evidencia} == {"dentro_de_limites", "alarma_alta", "sin_limites"}


@pytest.mark.parametrize("texto", ["PARADA, en reserva.", "Parada por mantenimiento"])
def test_parada_esperada(catalogo, texto):
    assert decision(catalogo, observacion(texto)).resultado == "SinNovedad"
    d = decision(catalogo, observacion(texto), lectura(20))
    assert d.resultado == "SinNovedad"
    assert "baja_esperada_por_parada" in {e.motivo for e in d.evidencia}


@pytest.mark.parametrize("filas", [
    [lectura(130)],
    [observacion("Pierde bastante por la empaquetadura", "goteo_empaquetadura")],
    [observacion("disparada, no arranca")],
])
def test_reserva_no_oculta_anomalia_independiente(catalogo, filas):
    assert decision(catalogo, observacion("parada, en reserva"), *filas).resultado == "Escalado"


@pytest.mark.parametrize(("variable", "texto", "resultado"), [
    ("ruido", "Hace un golpeteo metálico", "Escalado"),
    ("ruido", "No hace un golpeteo metálico", "SinNovedad"),
    ("ruido", "No hace un golpeteo metálico, pero tiene un ruido raro de rodamiento", "Escalado"),
    ("ruido", "No hace un golpeteo metálico ni tiene un ruido raro de rodamiento", "SinNovedad"),
    ("ruido", "No sé si hace un golpeteo metálico", "Indeterminado"),
    ("estado_correas", "Las correas no están flojas", "SinNovedad"),
    ("goteo_empaquetadura", "Gotea por la empaquetadura", "Indeterminado"),
    ("estado", "en marcha", "Indeterminado"),
    ("estado", "No está parada en reserva", "Indeterminado"),
    ("estado", "parada, en reserva y no arranca", "Escalado"),
    ("estado", "parada en reserva, algo raro", "Indeterminado"),
])
def test_interpretacion_cualitativa_acotada(catalogo, variable, texto, resultado):
    d = decision(catalogo, observacion(texto, variable))
    assert d.resultado == resultado
    assert all(e.observacion == texto for e in d.evidencia)


def test_observaciones_contradictorias_del_mismo_hallazgo(catalogo):
    afirma = observacion("Hace un golpeteo metálico", "ruido")
    niega = observacion("No hace un golpeteo metálico", "ruido")
    d = decision(catalogo, afirma, niega, lectura())
    assert d.resultado == "Indeterminado"
    assert "observacion_contradictoria" in {e.motivo for e in d.evidencia}
    assert decision(catalogo, afirma, niega, lectura(130)).resultado == "Escalado"


def test_marcha_y_parada_contradictorias_no_justifican_baja(catalogo):
    d = decision(catalogo, observacion("en marcha"), observacion("parada, en reserva"), lectura(20))
    assert d.resultado == "Indeterminado"
    assert "observacion_contradictoria" in {e.motivo for e in d.evidencia}


def test_marcha_y_disparo_son_contradictorios(catalogo):
    d = decision(catalogo, observacion("en marcha"), observacion("disparada"))
    assert d.resultado == "Indeterminado"
    assert {e.motivo for e in d.evidencia} == {"observacion_contradictoria"}


@pytest.mark.parametrize(("fila", "motivo"), [
    (lectura(110, "mm/s"), "unidad_incompatible"),
    (lectura(110, None), "unidad_ausente"),
    (lectura(None), "valor_ausente"),
    (lectura(71, "degC", variable="temperatura_rodamiento_ir"), "sin_limites"),
    (lectura(tag="DESCONOCIDO"), "equipo_desconocido"),
])
def test_dato_insuficiente(catalogo, fila, motivo):
    d = decision(catalogo, fila)
    assert d.resultado == "Indeterminado"
    assert d.evidencia[0].motivo == motivo


def test_lectura_local_sin_limite_solo_aporta_contexto(catalogo):
    ir = lectura(71, "degC", variable="temperatura_rodamiento_ir")
    assert decision(catalogo, ir, lectura()).resultado == "SinNovedad"
    assert decision(catalogo, ir, lectura(110, None)).resultado == "Indeterminado"


@pytest.mark.parametrize("catalogo", [None, {}, {"sensores": None}])
def test_catalogo_ausente(catalogo):
    d = decision(catalogo, lectura())
    assert d.resultado == "Indeterminado"
    assert d.evidencia[0].motivo == "catalogo_ausente"


@pytest.mark.parametrize("cambio", [
    {"alarma_baja": 130}, {"alarma_baja": 125}, {"alarma_alta": nan}, {"unidad": None},
])
def test_limites_inconsistentes(catalogo, cambio):
    catalogo["sensores"][0].update(cambio)
    d = decision(catalogo, lectura())
    assert d.resultado == "Indeterminado"
    assert d.evidencia[0].motivo == "limites_invalidos"


def test_limites_duplicados_no_se_eligen_por_orden(catalogo):
    catalogo["sensores"].append(dict(catalogo["sensores"][0], alarma_alta=109))
    d = decision(catalogo, lectura())
    assert d.resultado == "Indeterminado"
    assert d.evidencia[0].motivo == "limites_invalidos"


@pytest.mark.parametrize("valor", [nan, inf, -inf, True, "110"])
def test_valor_invalido(catalogo, valor):
    fila = FilaParte.model_construct(tag="B-101", variable="presion_descarga", valor=valor, unidad="kg/cm2")
    d = decision(catalogo, fila)
    assert d.resultado == "Indeterminado"
    assert d.evidencia[0].motivo == "valor_invalido"


def test_resultados_repetibles_y_entradas_intactas(catalogo):
    filas = [lectura(80), lectura(1, variable="presion_diferencial_local", tag="F-201A")]
    originales = deepcopy((filas, catalogo))
    assert clasificar_parte(filas, catalogo) == clasificar_parte(*originales)
    assert (filas, catalogo) == originales
    assert clasificar_parte([], catalogo) == ()
