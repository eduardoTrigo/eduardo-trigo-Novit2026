from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import sqlite3
from threading import Event
from unittest.mock import MagicMock

import httpx
import pytest
from planta_contrato import MensajeTablet, Topicos

from sistema.outbox_publisher import publicar_siguiente
from sistema.part_context import ClientePlanta
from sistema.part_worker import WorkerPartes
from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import procesar, respuesta, resultado
from test_security_messages import crear_nota


def catalogo():
    planta = definicion_planta()
    planta["sensores"].append({
        "tag": "B-102", "variable": "presion_descarga", "unidad": "kg/cm2",
        "alarma_baja": 90, "alarma_alta": 130,
    })
    return planta


def iniciar(tmp_path, texto, historial=None, definir=catalogo):
    notas, _, outbox, partes, _ = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, texto)
    worker = WorkerPartes(partes, definir, historial=historial) if historial else WorkerPartes(partes, definir)
    assert worker.procesar_siguiente()
    return nota, outbox, partes, worker


def mensaje(outbox):
    return MensajeTablet.model_validate_json(outbox.reclamar()["payload"])


@pytest.mark.parametrize(("texto", "estado"), [
    (
        "La 1 está en 112 kilogramos de descarga el rodamiento medio 59 grados con la pistola, "
        "ambien la bomba está en 1610 pc y de descarga, el filtro a medio 1,3 de diferencial.",
        "aclaracion_pendiente",
    ),
    (
        "La 1 estar en 102 kilogramos de descarga el rodamiento medio 59 grados con la pistola, "
        "ambiana bomba está en 1610 pc y de descarga, el filtro a medio 1,3 de diferencial.",
        "revision_manual",
    ),
], ids=["transcripcion-issue11", "transcripcion-5b"])
def test_aclaracion_depende_de_referencia_de_equipo_en_transcripcion(tmp_path, texto, estado):
    nota, outbox, partes, _ = iniciar(tmp_path, texto)
    turno = partes.obtener(nota.id)
    assert turno.estado == estado
    assert [(f.tag, f.variable, f.valor, f.unidad) for f in turno.filas] == [
        ("F-201A", "presion_diferencial", 1.3, "kg/cm2"),
    ]
    if estado == "aclaracion_pendiente":
        pregunta = mensaje(outbox)
        assert pregunta.espera_respuesta
        assert pregunta.pantalla.opciones == ["B-101 · la uno", "B-102 · la dos", "B-103 · la tres"]
    else:
        assert outbox.reclamar() is None


@pytest.mark.parametrize("texto", ["La bomba está en ciento veinte", "La bomba está en 120 de descarga"])
def test_equipo_ambiguo_pregunta_con_opciones_oficiales_sin_filas(tmp_path, texto):
    nota, outbox, partes, worker = iniciar(tmp_path, texto)
    turno = partes.obtener(nota.id)
    pregunta = mensaje(outbox)
    assert turno.estado == "aclaracion_pendiente" and not turno.filas
    assert pregunta.espera_respuesta and not pregunta.pantalla.filas
    assert pregunta.pantalla.opciones == ["B-101 · la uno", "B-102 · la dos", "B-103 · la tres"]
    seleccion = "La correcta es la uno." if "descarga" in texto else "B-101 · la uno"
    procesar(partes, worker, respuesta(turno, seleccion))
    version = 2
    if "descarga" not in texto:
        siguiente = partes.obtener(nota.id)
        assert siguiente.estado == "aclaracion_pendiente" and not siguiente.filas
        assert "variable" in mensaje(outbox).texto
        procesar(partes, worker, respuesta(siguiente, "Descarga.", identificador="VARIABLE"))
        version = 3
    nueva = mensaje(outbox)
    assert nueva.id == f"PARTE-{nota.id}-{version}" and nueva.nota_ids == [nota.id]
    assert not nueva.pantalla.opciones
    assert [(f.tag, f.valor, f.unidad) for f in nueva.pantalla.filas] == [("B-101", 120, "kg/cm2")]
    assert partes.obtener(nota.id).estado == "borrador_pendiente"


def test_diferencial_no_asigna_ninguna_alternativa_sin_respuesta_explicita(tmp_path):
    nota, outbox, partes, worker = iniciar(tmp_path, "El primario me dio dieciocho de diferencial")
    turno = partes.obtener(nota.id)
    pregunta = mensaje(outbox)
    assert turno.estado == "aclaracion_pendiente" and not turno.filas
    assert "18" in pregunta.texto and "1,8" in pregunta.texto
    procesar(partes, worker, respuesta(turno, "No sé si fue 18 o 1,8.", identificador="AMB"))
    assert partes.obtener(nota.id).version == 1 and resultado(partes, "AMB") == "insuficiente"
    assert outbox.pendientes() == 0
    procesar(partes, worker, respuesta(turno, "No. El filtro A estaba en uno coma ocho kilos, no en dieciocho. Arreglá eso."))
    nuevo = partes.obtener(nota.id)
    assert nuevo.estado == "borrador_pendiente" and nuevo.mensaje_id == f"PARTE-{nota.id}-2"
    assert nuevo.filas[0].valor == 1.8
    assert nuevo.contexto["resoluciones"][0]["respuesta_id"] == "RESP-001"


def test_confirmacion_del_candidato_numerico_vigente_despues_de_reinicio(tmp_path):
    nota, outbox, partes, _ = iniciar(tmp_path, "El primario me dio dieciocho de diferencial")
    pregunta = mensaje(outbox)
    assert "Confirmá solo si" in pregunta.texto
    assert [(f.tag, f.valor, f.unidad) for f in pregunta.pantalla.filas] == [
        ("F-201A", 18, "kg/cm2"),
    ]
    _, _, _, reabiertas, worker = preparar(tmp_path)
    turno = reabiertas.obtener(nota.id)
    procesar(reabiertas, worker, respuesta(turno, "Correcto, dale."))
    nueva = reabiertas.obtener(nota.id)
    assert nueva.version == 2 and nueva.estado == "borrador_pendiente"
    assert nueva.filas == pregunta.pantalla.filas
    procesar(reabiertas, worker, respuesta(nueva, "Sí, confirmo.", identificador="FINAL"))
    assert reabiertas.obtener(nota.id).estado == "confirmado"


def test_aceptacion_generica_sin_candidato_publicado_no_resuelve_valor(tmp_path):
    def sin_unidad():
        planta = catalogo()
        planta["sensores"] = [s for s in planta["sensores"] if s["tag"] != "F-201A"]
        return planta

    nota, outbox, partes, worker = iniciar(
        tmp_path, "El primario me dio dieciocho de diferencial", definir=sin_unidad,
    )
    assert not mensaje(outbox).pantalla.filas
    turno = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(turno, "Sí, confirmo."))
    assert resultado(partes, "RESP-001") == "insuficiente"
    assert partes.obtener(nota.id) == replace(turno, interacciones=1)


def test_diferencial_sin_equipo_resuelve_ambas_dudas_en_orden(tmp_path):
    nota, outbox, partes, worker = iniciar(tmp_path, "El diferencial está en dieciocho")
    turno = partes.obtener(nota.id)
    assert mensaje(outbox).pantalla.opciones == ["F-201A · el filtro A", "F-201B · el filtro B"]
    procesar(partes, worker, respuesta(turno, "Era el filtro A."))
    siguiente = partes.obtener(nota.id)
    assert siguiente.estado == "aclaracion_pendiente" and not siguiente.filas
    assert "18 o 1,8" in mensaje(outbox).texto
    procesar(partes, worker, respuesta(siguiente, "Uno coma ocho.", identificador="VALOR"))
    final = partes.obtener(nota.id)
    assert final.estado == "borrador_pendiente" and final.version == 3
    assert [(f.tag, f.valor) for f in final.filas] == [("F-201A", 1.8)]


def test_equipo_ambiguo_en_observacion_de_parada_del_simulador(tmp_path):
    nota, outbox, partes, worker = iniciar(tmp_path, "Listo, la bomba está parada en reservar.")
    turno = partes.obtener(nota.id)
    pregunta = mensaje(outbox)
    assert not pregunta.pantalla.filas and "B-103 · la tres" in pregunta.pantalla.opciones
    procesar(partes, worker, respuesta(turno, "Era la tres."))
    final = partes.obtener(nota.id)
    assert final.estado == "revision_manual"
    assert [(f.tag, f.variable) for f in final.filas] == [("B-103", "estado")]


def test_aclaracion_publicada_y_correlacionada_deriva_a_revision_si_resta_una_negacion(tmp_path):
    texto = "La bomba está en 120 de descarga. La dos no está en 110 kilos de descarga."
    nota, outbox, partes, _ = iniciar(tmp_path, texto)
    turno = partes.obtener(nota.id)
    assert turno.estado == "aclaracion_pendiente" and turno.contexto["no_interpretada"]
    cliente = MagicMock()
    cliente.conectado.is_set.return_value = True
    cliente.publish.return_value.rc = 0
    cliente.publish.return_value.is_published.return_value = True
    assert publicar_siguiente(outbox, cliente)
    envio = cliente.publish.call_args.kwargs
    pregunta = MensajeTablet.model_validate_json(envio["payload"], strict=True)
    assert envio["topic"] == Topicos.TABLET and envio["qos"] == 1
    assert pregunta.espera_respuesta and pregunta.id == turno.mensaje_id
    assert pregunta.nota_ids == [nota.id] and not pregunta.pantalla.filas

    notas, seguridad, outbox, partes, worker = preparar(tmp_path)
    assert partes.buscar_por_mensaje(pregunta.id) == turno
    seleccion = respuesta(turno, "Era la uno.")
    procesar(partes, worker, seleccion)
    final = partes.obtener(nota.id)
    assert final.estado == "revision_manual" and final.version == turno.version
    assert [(f.tag, f.valor) for f in final.filas] == [("B-101", 120)]
    assert final.contexto["pendientes"] == [] and final.contexto["no_interpretada"]
    assert final.contexto["resoluciones"][0]["respuesta_id"] == seleccion.id
    assert notas.obtener_transcripcion(nota.id) == texto
    assert seguridad.pendientes() == outbox.pendientes() == 0
    tardia = respuesta(turno, "Sí, confirmo.", identificador="RESP-TARDIA")
    procesar(partes, worker, tardia)
    assert resultado(partes, tardia.id) == "desfasada" and partes.obtener(nota.id) == final


def test_insuficiente_repetida_no_publica_otra_pregunta(tmp_path):
    nota, outbox, partes, worker = iniciar(tmp_path, "El primario me dio dieciocho de diferencial")
    turno = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(turno, "¿Cómo? No te sigo.", identificador="R1"))
    procesar(partes, worker, respuesta(turno, "¿CÓMO?  No te sigo.", identificador="R2"))
    assert resultado(partes, "R1") == "insuficiente" and resultado(partes, "R2") == "duplicada"
    assert partes.obtener(nota.id) == replace(turno, interacciones=1) and outbox.pendientes() == 1


def test_unidad_ausente_sin_fuente_oficial_se_pregunta(tmp_path):
    def sin_unidad():
        planta = catalogo()
        del planta["equipos"][1]["manometro_local"]
        return planta

    nota, outbox, partes, worker = iniciar(tmp_path, "La dos está en 1640 de descarga", definir=sin_unidad)
    turno = partes.obtener(nota.id)
    pregunta = mensaje(outbox)
    assert "unidad" in pregunta.texto.lower() and not pregunta.pantalla.filas
    procesar(partes, worker, respuesta(turno, "La dos está en psi."))
    assert partes.obtener(nota.id).filas[0].unidad == "psi"
    assert partes.obtener(nota.id).estado == "borrador_pendiente"


@pytest.mark.parametrize("fuente,estado", [
    ([], "vacio"), ([110], "insuficiente"), (httpx.ReadTimeout("prueba"), "inaccesible"),
    ([110, 120], "compatible"),
])
def test_nota_clara_no_pregunta_de_mas_con_contexto_variable(tmp_path, fuente, estado):
    def historico(tag, variable, ts):
        assert ts.utcoffset() is not None
        if isinstance(fuente, Exception):
            raise fuente
        return [{"ts": (ts - timedelta(days=i + 1)).isoformat(), "valor": n}
                for i, n in enumerate(fuente)]

    nota, outbox, partes, _ = iniciar(tmp_path, "La del medio está en 1640 de descarga", historico)
    borrador = mensaje(outbox)
    assert borrador.espera_respuesta and not borrador.pantalla.opciones
    assert [(f.tag, f.valor, f.unidad) for f in borrador.pantalla.filas] == [("B-102", 1640, "psi")]
    evidencia = partes.obtener(nota.id).contexto["evidencia"][0]
    assert evidencia["historico"]["estado"] == estado
    assert evidencia["comparado"] == pytest.approx(115.3, rel=0.001)


def test_varias_lecturas_claras_sin_pregunta_previa(tmp_path):
    nota, outbox, partes, _ = iniciar(
        tmp_path, "La dos está en 1640 de descarga. El primario dio uno coma ocho de diferencial.",
        lambda *_: [],
    )
    borrador = mensaje(outbox)
    assert borrador.espera_respuesta and not borrador.pantalla.opciones
    assert [(f.tag, f.unidad) for f in borrador.pantalla.filas] == [("B-102", "psi"), ("F-201A", "kg/cm2")]
    assert partes.obtener(nota.id).version == 1


def test_presion_explicita_en_bar_conserva_unidad_y_convierte_solo_para_comparar(tmp_path):
    def historial(tag, variable, ts):
        return [{"ts": (ts - timedelta(days=i + 1)).isoformat(), "valor": n}
                for i, n in enumerate((110, 120))]

    nota, outbox, partes, _ = iniciar(tmp_path, "La dos está en 113 bares de descarga", historial)
    borrador = mensaje(outbox)
    assert not borrador.pantalla.opciones
    assert borrador.pantalla.filas[0].unidad == "bar" and borrador.pantalla.filas[0].valor == 113
    evidencia = partes.obtener(nota.id).contexto["evidencia"][0]
    assert evidencia["unidad"] == "kg/cm2" and evidencia["comparado"] == pytest.approx(115.23, rel=0.001)


@pytest.mark.parametrize("fuente,categoria", [([100, 110], "historico"), ([], "limites")])
def test_fuera_de_rango_se_confirma_antes_del_borrador(tmp_path, fuente, categoria):
    def historico(tag, variable, ts):
        return [{"ts": (ts - timedelta(days=i + 1)).isoformat(), "valor": n}
                for i, n in enumerate(fuente)]

    valor = 1640 if fuente else 2000
    nota, outbox, partes, worker = iniciar(tmp_path, f"La dos está en {valor} de descarga", historico)
    turno = partes.obtener(nota.id)
    pregunta = mensaje(outbox)
    assert turno.estado == "aclaracion_pendiente" and pregunta.pantalla.opciones
    assert turno.contexto["evidencia"][0][categoria]["estado"] == "fuera_de_rango"
    procesar(partes, worker, respuesta(turno, "Sí, confirmo."))
    borrador = partes.obtener(nota.id)
    assert borrador.estado == "borrador_pendiente" and borrador.version == 2
    assert not mensaje(outbox).pantalla.opciones
    procesar(partes, worker, respuesta(borrador, "Sí, confirmo.", identificador="FINAL"))
    assert partes.obtener(nota.id).estado == "confirmado"


def test_reintroducir_un_fuera_de_rango_corregido_exige_confirmacion(tmp_path):
    def historico(tag, variable, ts):
        return [{"ts": (ts - timedelta(days=i + 1)).isoformat(), "valor": n}
                for i, n in enumerate((100, 120))]

    nota, outbox, partes, worker = iniciar(tmp_path, "La dos está en 2000 de descarga", historico)
    turno = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(turno, "No. La dos estaba en 1640 psi, no en 2000."))
    v2 = partes.obtener(nota.id)
    assert v2.estado == "borrador_pendiente"
    procesar(partes, worker, respuesta(v2, "No. La dos estaba en 2000 psi, no en 1640.", identificador="VOLVER"))
    v3 = partes.obtener(nota.id)
    assert v3.estado == "aclaracion_pendiente" and v3.version == 3
    procesar(partes, worker, respuesta(v3, "Sí, confirmo.", identificador="PRESUPUESTO"))
    assert partes.obtener(nota.id).estado == "revision_manual" and outbox.pendientes() == 3


def test_historian_api_ventana_y_reintento_acotados(monkeypatch):
    llamadas = []

    def get(url, **opciones):
        llamadas.append((url, opciones))
        if len(llamadas) == 1:
            raise httpx.ReadTimeout("prueba")
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    ts = crear_nota().ts
    assert ClientePlanta("http://sim:8100").historial("B-102", "presion_descarga", ts) == []
    assert len(llamadas) == 2
    for url, opciones in llamadas:
        assert url == "http://sim:8100/api/historian/B-102/presion_descarga"
        assert opciones["params"] == {"desde": (ts - timedelta(days=45)).isoformat(), "hasta": ts.isoformat()}
        assert 0 < opciones["timeout"] <= 2


def test_reinicio_rollback_duplicados_y_presupuesto_compartido(tmp_path):
    nota, outbox, partes, _ = iniciar(tmp_path, "La bomba está en 120 de descarga")
    turno = partes.obtener(nota.id)
    r = respuesta(turno, "Era la uno.")
    assert partes.guardar_respuesta(r)
    with sqlite3.connect(partes.ruta) as db:
        db.execute("""CREATE TRIGGER falla_turno BEFORE INSERT ON outbox
                       BEGIN SELECT RAISE(ABORT, 'fallo de prueba'); END""")
    _, _, _, recuperado, worker = preparar(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert recuperado.obtener(nota.id) == turno and resultado(recuperado, r.id) is None
    with sqlite3.connect(partes.ruta) as db:
        db.execute("DROP TRIGGER falla_turno")
    assert worker.procesar_siguiente()
    assert not recuperado.guardar_respuesta(r)
    procesar(recuperado, worker, r.model_copy(update={"id": "VIEJA"}))
    assert resultado(recuperado, "VIEJA") == "desfasada"
    v2 = recuperado.obtener(nota.id)
    procesar(recuperado, worker, respuesta(v2, "No, la uno estaba en 115 kilos.", identificador="R2"))
    v3 = recuperado.obtener(nota.id)
    assert v3.version == 3 and v3.filas[0].valor == 115
    procesar(recuperado, worker, respuesta(v3, "No, la uno estaba en 116 kilos.", identificador="R3"))
    assert recuperado.obtener(nota.id).estado == "revision_manual"
    assert outbox.pendientes() == 3


@pytest.mark.parametrize("condicion,texto", [
    ("S1", "Hay un chorro de agua a presión"),
    ("S2", "Se siente olor a gas"),
    ("S3", "Sale humo de la bomba"),
    ("S4", "Hay un operador herido"),
])
def test_historian_lento_no_bloquea_seguridad(tmp_path, condicion, texto):
    notas, seguridad, _, partes, _ = preparar(tmp_path)
    entrada = Event()
    liberar = Event()

    def lento(tag, variable, ts):
        entrada.set()
        assert liberar.wait(timeout=5)
        return []

    nota = crear_nota()
    transcribir_nota(notas, nota, "La dos está en 1640 de descarga")
    worker = WorkerPartes(partes, catalogo, historial=lento)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pendiente = pool.submit(worker.procesar_siguiente)
        try:
            assert entrada.wait(timeout=3)
            alarma = nota.model_copy(update={"id": "NOTA-SEG"})
            transcribir_nota(notas, alarma, texto)
            assert seguridad.reclamar()["id"] == f"SEG-{alarma.id}-{condicion}"
            assert seguridad.reclamar()["id"] == f"TABLET-SEG-{alarma.id}"
        finally:
            liberar.set()
        assert pendiente.result(timeout=5)
