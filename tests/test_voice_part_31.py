import time
from datetime import timedelta

import pytest

from sistema.execution_budget import ConsumoEjecucion, ControlEjecucion, PresupuestoEjecucion
from sistema.part_agent import AgentePartes
from sistema.part_clarification import resolver
from sistema.part_extraction import extraer_parte, interpretar_numero
from sistema.part_context import verificar_lectura
from sistema.plant_tool_models import HistorialSensores, Muestra
from test_part_clarification import catalogo
from test_part_extraction import definicion_planta
from test_security_messages import crear_nota


def derivar(texto, **kwargs):
    nota = crear_nota()
    control = ControlEjecucion(nota.id, "prueba", PresupuestoEjecucion(), ConsumoEjecucion(),
                               inicio=time.monotonic(), reloj=time.monotonic, cancelada=lambda: False)
    return AgentePartes(definicion_planta, **kwargs).nota(control, nota, texto)


@pytest.mark.parametrize(("texto", "variable", "observacion"), [
    ("Saqué foto de la tres, tiene las correas flojas.", "estado_correas", "tiene las correas flojas"),
    ("Saqué foto de la tres y tiene el visor de aceite bajo.", "nivel_aceite_visor",
     "tiene el visor de aceite bajo"),
    ("Saqué foto de la tres, no arranca.", "estado", "no arranca"),
    ("Tomé una foto de la tres que tiene las correas flojas.", "estado_correas", "que tiene las correas flojas"),
    ("Saqué foto de la tres parada en reserva.", "estado", "parada en reserva"),
])
def test_foto_y_observacion_en_la_misma_frase(texto, variable, observacion):
    plan = derivar(texto)
    fila, = plan.filas
    assert plan.estado == "borrador_pendiente"
    assert (fila.tag, fila.variable, fila.valor, fila.unidad, fila.observacion) == (
        "B-103", variable, None, None, observacion,
    )


@pytest.mark.parametrize(("texto", "estado", "lecturas"), [
    ("Saqué foto de las correas de la tres.", "sin_contenido", []),
    ("Foto de las correas de la tres.", "sin_contenido", []),
    ("Saqué foto de la dos, está en 1640 psi de descarga.", "borrador_pendiente",
     [("B-102", "presion_descarga_local", 1640)]),
    ("Saqué foto de la dos mil seiscientos cuarenta psi de descarga.", "borrador_pendiente",
     [("B-102", "presion_descarga_local", 1640)]),
    ("Saqué foto de las correas de la tres, la dos tiene ruido. La uno 112 kilos de descarga.",
     "borrador_pendiente", [("B-102", "ruido", None), ("B-101", "presion_descarga_local", 112)]),
    ("Saqué foto de la bomba, tiene las correas flojas.", "aclaracion_pendiente", []),
    ("Saqué foto de la tres o la dos, tiene las correas flojas.", "aclaracion_pendiente", []),
    ("Saqué foto de la tres, no está en 115 bar de descarga.", "revision_manual", []),
])
def test_clausula_fotografica_conserva_limites_de_la_lectura(texto, estado, lecturas):
    plan = derivar(texto)
    assert plan.estado == estado
    assert [(f.tag, f.variable, f.valor) for f in plan.filas] == lecturas
    if estado == "aclaracion_pendiente":
        assert plan.contexto["pendientes"][0]["tipo"] == "equipo"


def test_lectura_mixta_con_stt_imperfecto_conserva_filas_y_pregunta_equipo_y_unidad():
    plan = derivar(
        "La 1 estar en 102 kilogramos de descarga el rodamiento medio 59 grados con la pistola, "
        "ambiana bomba está en 1610 pc y de descarga, el filtro a medio 1,3 de diferencial."
    )
    assert plan.estado == "aclaracion_pendiente"
    assert [(f.tag, f.variable, f.valor, f.unidad) for f in plan.filas] == [
        ("B-101", "presion_descarga_local", 102, "kg/cm2"),
        ("B-101", "temperatura_rodamiento_ir", 59, "degC"),
        ("F-201A", "presion_diferencial", 1.3, "kg/cm2"),
    ]
    duda, = plan.contexto["pendientes"]
    assert duda["tipo"] == "equipo"
    resultado, filas, unidad = resolver(duda, "B-102", definicion_planta(), plan.filas)
    assert resultado == "resuelta" and filas == plan.filas
    assert unidad["tipo"] == "unidad" and unidad["valor"] == 1610 and unidad["unidad"] is None
    resuelta, filas, siguiente = resolver(unidad, "psi", definicion_planta(), filas)
    assert resuelta == "resuelta" and siguiente is None
    assert [(f.tag, f.valor, f.unidad) for f in filas[-1:]] == [("B-102", 1610, "psi")]


def test_habla_cualitativa_reportable_conserva_valor_ausente_y_timestamp():
    plan = derivar("La tres está parada, en reserva.")
    assert plan.estado == "borrador_pendiente"
    assert plan.mensaje.ts == crear_nota().ts
    assert [(f.tag, f.variable, f.valor, f.unidad, f.observacion) for f in plan.mensaje.pantalla.filas] == [
        ("B-103", "estado", None, None, "esta parada en reserva"),
    ]


def test_conflicto_conserva_otra_fila_y_exige_una_lectura_explicita():
    plan = derivar("La uno 112 kilos de descarga. La uno 112 kilos de descarga. "
                   "La uno 114 kilos de descarga. La dos 1640 psi de descarga.")
    assert plan.estado == "aclaracion_pendiente"
    assert [(f.tag, f.valor) for f in plan.filas] == [("B-102", 1640)]
    duda, = plan.contexto["pendientes"]
    assert duda["tipo"] == "conflicto"
    assert resolver(duda, "Confirmo", definicion_planta(), plan.filas)[0] == "insuficiente"
    resuelta, filas, _ = resolver(duda, "114 kilos", definicion_planta(), plan.filas)
    assert resuelta == "resuelta"
    assert [(f.tag, f.valor) for f in filas] == [("B-102", 1640), ("B-101", 114)]


def test_fragmento_parcial_preserva_motivo_y_no_confirma():
    plan = derivar("Confirmo. La dos 1640 psi de descarga. La uno no está en 115 kilos de descarga.")
    assert plan.estado == "revision_manual"
    assert [(f.tag, f.valor) for f in plan.filas] == [("B-102", 1640)]
    assert plan.contexto["segmentos_no_interpretados"] == [
        {"texto": "no esta en 115 kilos de descarga", "motivo": "lectura_no_resuelta"},
    ]


def test_numero_mixto_distingue_decimal_y_miles():
    assert interpretar_numero("1.640,5") == interpretar_numero("1,640.5") == 1640.5
    assert interpretar_numero("1.640") is None
    fila, = extraer_parte("La dos 1.640,5 psi de descarga", definicion_planta()).filas
    assert fila.valor == 1640.5
    assert interpretar_numero("-1 8", decimal_implicito=True) == -1.8


def test_sujeto_coordinado_no_atribuye_lectura_a_ultimo_equipo():
    plan = derivar("La uno o la dos está en 115 kilos de descarga. La tres vibra y hace ruido.")
    duda, = plan.contexto["pendientes"]
    assert duda["tipo"] == "equipo" and duda["opciones"] == ["B-101", "B-102"]
    assert [(f.tag, f.variable) for f in plan.filas] == [("B-103", "vibracion"), ("B-103", "ruido")]


def test_repeticion_equivalente_en_otras_unidades_conserva_original():
    filas = extraer_parte("La uno 100 kilos de descarga. La uno 1422,33 psi de descarga.",
                          definicion_planta()).filas
    assert [(f.tag, f.valor, f.unidad) for f in filas] == [("B-101", 100, "kg/cm2")]


def test_historial_truncado_conserva_cobertura_sin_decidir_por_rango_parcial():
    fecha = crear_nota().ts
    planta = catalogo()
    fila, = extraer_parte("La dos 1640 psi de descarga", planta).filas
    serie = HistorialSensores(tag=fila.tag, variable="presion_descarga", unidad="kg/cm2",
                              desde=fecha - timedelta(days=45), hasta=fecha,
                              items=[Muestra(ts=fecha - timedelta(days=i), valor=float(valor))
                                     for i, valor in enumerate((100, 105), start=1)],
                              total=6, truncado=True)
    evidencia = verificar_lectura(fila, fecha, planta, lambda *_: serie)
    historico = evidencia["historico"]
    assert historico["estado"] == "incompleto" and historico["truncado"]
    assert historico["total"] == 6 and historico["muestras"] == 2
    assert historico["desde_fuente"] == serie.desde.isoformat() and "minimo" not in historico
    assert evidencia["limites"]["estado"] == "compatible"
