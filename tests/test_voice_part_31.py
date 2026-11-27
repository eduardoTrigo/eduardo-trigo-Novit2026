import time
from datetime import timedelta

import pytest
from planta_contrato import MensajeTablet

from sistema.execution_budget import ConsumoEjecucion, ControlEjecucion, PresupuestoEjecucion
from sistema.part_agent import AgentePartes
from sistema.part_clarification import resolver
from sistema.part_extraction import extraer_parte, interpretar_numero
from sistema.part_context import verificar_lectura
from sistema.plant_tool_models import HistorialSensores, Muestra
from test_part_clarification import catalogo
from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import procesar, respuesta
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


@pytest.mark.parametrize(("texto", "filas"), [
    ("La uno 112 kilos de descarga. La dos hace ruido en el motor.", [
        ("B-101", "presion_descarga_local", 112, "kg/cm2", None),
        ("B-102", "ruido", None, None, "hace ruido en el motor"),
    ]),
    ("La dos hace ruido en el rodamiento.", [
        ("B-102", "ruido", None, None, "hace ruido en el rodamiento"),
    ]),
    ("La dos vibra en  el  motor.", [
        ("B-102", "vibracion", None, None, "vibra en  el  motor"),
    ]),
    ("La tres suena raro en el rodamiento.", [
        ("B-103", "ruido", None, None, "suena raro en el rodamiento"),
    ]),
    ("La dos hace ruido en el motor y el rodamiento.", [
        ("B-102", "ruido", None, None, "hace ruido en el motor y el rodamiento"),
    ]),
    ("La dos no hace ruido en el motor.", [
        ("B-102", "ruido", None, None, "no hace ruido en el motor"),
    ]),
    ("La dos hace ruido en el motor y vibra en el rodamiento.", [
        ("B-102", "ruido", None, None, "hace ruido en el motor"),
        ("B-102", "vibracion", None, None, "vibra en el rodamiento"),
    ]),
    ("La uno 112 kilos de descarga el rodamiento midió 59 grados.", [
        ("B-101", "presion_descarga_local", 112, "kg/cm2", None),
        ("B-101", "temperatura_rodamiento_ir", 59, "degC", None),
    ]),
    ("La uno 112 kilos de descarga y el motor 65 grados.", [
        ("B-101", "presion_descarga_local", 112, "kg/cm2", None),
        ("B-101", "temperatura_motor_ir", 65, "degC", None),
    ]),
    ("La uno 112 kilos de descarga el rodamiento medio 59 grados con la pistola.", [
        ("B-101", "presion_descarga_local", 112, "kg/cm2", None),
        ("B-101", "temperatura_rodamiento_ir", 59, "degC", None),
    ]),
    ("La dos hace ruido en el motor y el rodamiento midió cincuenta y nueve grados.", [
        ("B-102", "ruido", None, None, "hace ruido en el motor"),
        ("B-102", "temperatura_rodamiento_ir", 59, "degC", None),
    ]),
    ("La dos hace ruido en el motor, el rodamiento midió 59 grados.", [
        ("B-102", "ruido", None, None, "hace ruido en el motor"),
        ("B-102", "temperatura_rodamiento_ir", 59, "degC", None),
    ]),
    ("Saqué foto de la dos, hace ruido en el motor. La tres 110 bar de descarga.", [
        ("B-102", "ruido", None, None, "hace ruido en el motor"),
        ("B-103", "presion_descarga_local", 110, "bar", None),
    ]),
    ("Saqué foto de la dos y el rodamiento midió 59 grados.", [
        ("B-102", "temperatura_rodamiento_ir", 59, "degC", None),
    ]),
    ("La dos hace ruido sobre el motor La tres 110 bar de descarga.", [
        ("B-102", "ruido", None, None, "hace ruido sobre el motor"),
        ("B-103", "presion_descarga_local", 110, "bar", None),
    ]),
])
def test_segmentacion_preserva_observaciones_y_lecturas_de_componentes(texto, filas):
    plan = derivar(texto)
    assert plan.estado == "borrador_pendiente"
    assert [(f.tag, f.variable, f.valor, f.unidad, f.observacion) for f in plan.filas] == filas
    assert not plan.contexto["no_interpretada"]
    assert plan.contexto["pendientes"] == plan.contexto["segmentos_no_interpretados"] == []
    assert plan.mensaje.espera_respuesta and plan.mensaje.pantalla.filas == plan.filas


@pytest.mark.parametrize(("texto", "segmento"), [
    ("La uno 112 kilos de descarga el rodamiento midió.", "el rodamiento midio"),
    ("La uno 112 kilos de descarga el rodamiento.", "el rodamiento"),
    ("La uno 112 kilos de descarga, el motor no midió 65 grados.", "el motor no midio 65 grados"),
    ("La uno 112 kilos de descarga y el motor midió aproximadamente 65 grados.",
     "el motor midio aproximadamente 65 grados"),
    ("La uno 112 kilos de descarga. La dos el rodamiento o el motor midió 65 grados.",
     "el rodamiento o el motor midio 65 grados"),
])
def test_lectura_incompleta_o_incierta_de_componente_conserva_revision(texto, segmento):
    plan = derivar(texto)
    assert plan.estado == "revision_manual" and plan.mensaje is None
    assert [(f.tag, f.variable, f.valor) for f in plan.filas] == [("B-101", "presion_descarga_local", 112)]
    assert plan.contexto["no_interpretada"] and plan.contexto["pendientes"] == []
    assert plan.contexto["segmentos_no_interpretados"] == [{"texto": segmento, "motivo": "lectura_no_resuelta"}]


def test_observacion_de_componente_con_equipo_ambiguo_pide_aclaracion_completa():
    plan = derivar("La uno o la dos hace ruido en el motor.")
    assert plan.estado == "aclaracion_pendiente" and not plan.filas
    duda, = plan.contexto["pendientes"]
    assert duda == {"tipo": "equipo", "clausula": "hace ruido en el motor", "opciones": ["B-101", "B-102"]}
    assert not plan.contexto["no_interpretada"] and plan.contexto["segmentos_no_interpretados"] == []
    assert plan.mensaje.espera_respuesta and len(plan.mensaje.pantalla.opciones) == 2
    estado, filas, siguiente = resolver(duda, "B-102", definicion_planta(), plan.filas)
    assert estado == "resuelta" and siguiente is None
    assert [(f.tag, f.variable, f.observacion) for f in filas] == [
        ("B-102", "ruido", "hace ruido en el motor"),
    ]


def test_borrador_mixto_con_componente_se_confirma_y_recupera(tmp_path):
    notas, _, outbox, partes, worker = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, "La uno 112 kilos de descarga. La dos hace ruido en el motor.")
    assert worker.procesar_siguiente()
    borrador = MensajeTablet.model_validate_json(outbox.reclamar()["payload"])
    assert borrador.ts == nota.ts and borrador.espera_respuesta
    assert [(f.tag, f.variable, f.valor, f.unidad, f.observacion) for f in borrador.pantalla.filas] == [
        ("B-101", "presion_descarga_local", 112, "kg/cm2", None),
        ("B-102", "ruido", None, None, "hace ruido en el motor"),
    ]
    _, _, _, partes, worker = preparar(tmp_path)
    procesar(partes, worker, respuesta(partes.obtener(nota.id), "Confirmo"))
    confirmado = preparar(tmp_path)[3].obtener(nota.id)
    assert confirmado.estado == "confirmado" and confirmado.filas == borrador.pantalla.filas


@pytest.mark.parametrize("componente", ["motor", "rodamiento"])
@pytest.mark.parametrize(("separador", "articulo", "medida"), [
    (" y ", "", "65 grados"),
    (" ", "", "midió sesenta y cinco grados"),
    (", ", "", "65 grados"),
    (" y ", "el ", "midió sesenta y cinco grados"),
    (" ", "el ", "65 grados"),
    (", ", "el ", "midió sesenta y cinco grados"),
])
def test_cierre_componente_independiente_con_omisiones_de_stt(componente, separador, articulo, medida):
    plan = derivar(f"La uno 112 kilos de descarga{separador}{articulo}{componente} {medida}.")
    assert plan.estado == "borrador_pendiente"
    assert [(f.tag, f.variable, f.valor, f.unidad) for f in plan.filas] == [
        ("B-101", "presion_descarga_local", 112, "kg/cm2"),
        ("B-101", f"temperatura_{componente}_ir", 65, "degC"),
    ]
    assert plan.contexto["pendientes"] == [] and not plan.contexto["no_interpretada"]
    assert plan.mensaje.pantalla.filas == plan.filas


@pytest.mark.parametrize(("texto", "tag_motor", "incluye_descarga"), [
    ("La uno 112 kilos de descarga y saqué foto del manómetro, el motor 65 grados.", "B-101", True),
    ("La uno 112 kilos de descarga y saqué foto del manómetro y motor 65 grados.", "B-101", True),
    ("La uno 112 kilos de descarga y saqué foto del manómetro, tomé otra foto del visor, el motor 65 grados.",
     "B-101", True),
    ("La uno 112 kilos de descarga y saqué foto del manómetro, la dos el motor 65 grados.", "B-102", True),
    ("La uno 112 kilos de descarga y saqué foto del manómetro y la dos el motor 65 grados.", "B-102", True),
    ("Saqué foto de la uno, motor 65 grados.", "B-101", False),
    ("La uno 112 kilos de descarga y motor 65 grados y saqué foto del manómetro.", "B-101", True),
    ("La uno 112 kilos de descarga y saqué foto debajo de la dos, el motor 65 grados.", "B-101", True),
    ("Saqué foto de la uno frente a la dos, motor 65 grados.", "B-101", False),
    ("Saqué foto de la uno, frente a la dos, motor 65 grados.", "B-101", False),
])
def test_cierre_foto_no_cambia_medicion_valida(texto, tag_motor, incluye_descarga):
    plan = derivar(texto)
    filas = [("B-101", "presion_descarga_local", 112)] if incluye_descarga else []
    assert plan.estado == "borrador_pendiente"
    assert [(f.tag, f.variable, f.valor) for f in plan.filas] == [
        *filas, (tag_motor, "temperatura_motor_ir", 65),
    ]
    assert not plan.contexto["no_interpretada"] and plan.contexto["pendientes"] == []
    assert plan.mensaje.pantalla.filas == plan.filas


@pytest.mark.parametrize(("separador", "lugar"), [
    (" ", "debajo de la dos"), (" ", "frente a la dos"), (", ", "debajo de la dos"),
])
def test_cierre_foto_espacial_no_define_equipo_para_observacion(separador, lugar):
    plan = derivar(f"Saqué foto de un goteo{separador}{lugar}, hace ruido.")
    assert plan.estado == "revision_manual" and not plan.filas and plan.mensaje is None
    assert plan.contexto["no_interpretada"] and plan.contexto["pendientes"] == []
    assert plan.contexto.get("segmentos_no_interpretados", []) == [
        {"texto": "hace ruido", "motivo": "lectura_no_resuelta"},
    ]


def test_cierre_conjuncion_despues_de_foto_con_equipo_explicito():
    plan = derivar("Saqué foto de la uno y la dos vibra en el motor.")
    assert plan.estado == "borrador_pendiente"
    assert [(f.tag, f.variable, f.observacion) for f in plan.filas] == [
        ("B-102", "vibracion", "vibra en el motor"),
    ]
    assert plan.contexto["pendientes"] == [] and not plan.contexto["no_interpretada"]


@pytest.mark.parametrize(("texto", "estado", "filas"), [
    ("La uno 112 kilos de descarga y saqué foto de la dos, el motor 65 grados.",
     "aclaracion_pendiente", [("B-101", "presion_descarga_local", 112)]),
    ("La uno o la dos hace ruido en motor y saqué foto de la dos, el rodamiento 65 grados.",
     "aclaracion_pendiente", []),
    ("La uno 112 kilos de descarga y saqué foto del manómetro. El motor 65 grados.",
     "revision_manual", [("B-101", "presion_descarga_local", 112)]),
    ("Saqué foto de la uno. El rodamiento 65 grados.", "revision_manual", []),
    ("La uno 112 kilos de descarga y saqué foto del manómetro, el motor no midió 65 grados.",
     "revision_manual", [("B-101", "presion_descarga_local", 112)]),
    ("La uno 112 kilos de descarga y saqué foto del manómetro, el motor midió.",
     "revision_manual", [("B-101", "presion_descarga_local", 112)]),
])
def test_cierre_foto_no_resuelve_dudas_ni_propaga_contexto_entre_oraciones(texto, estado, filas):
    plan = derivar(texto)
    assert plan.estado == estado
    assert [(f.tag, f.variable, f.valor) for f in plan.filas] == filas
    if estado == "aclaracion_pendiente":
        assert plan.mensaje.espera_respuesta
        assert all(d["opciones"] == ["B-101", "B-102"] for d in plan.contexto["pendientes"])
        assert not plan.contexto["no_interpretada"]
    else:
        assert plan.mensaje is None and plan.contexto["no_interpretada"]


@pytest.mark.parametrize("complemento", ["en motor", "en el motor", "en el motor y rodamiento"])
def test_cierre_componente_en_observacion_conserva_contexto(complemento):
    plan = derivar(f"La dos hace ruido {complemento}.")
    assert plan.estado == "borrador_pendiente"
    fila, = plan.filas
    assert (fila.tag, fila.variable, fila.valor, fila.unidad, fila.observacion) == (
        "B-102", "ruido", None, None, f"hace ruido {complemento}",
    )
    assert not plan.contexto["no_interpretada"] and plan.contexto["pendientes"] == []


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
