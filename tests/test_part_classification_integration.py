import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from unittest.mock import MagicMock

import httpx
import pytest
from planta_contrato import FilaParte

from sistema import part_classification_worker
from sistema.part_classification_store import AlmacenClasificacionB1
from sistema.part_classification_worker import WorkerClasificacionB1
from sistema.part_context import ClientePlanta
from sistema.part_messages import crear_borrador
from test_part_clarification import iniciar
from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import preparar_borrador, procesar, respuesta, resultado
from test_security_messages import crear_nota


def consultar(almacen, sql):
    with closing(sqlite3.connect(almacen.ruta)) as db:
        db.row_factory = sqlite3.Row
        return [dict(fila) for fila in db.execute(sql)]


def trabajos(almacen):
    return consultar(almacen, "SELECT * FROM trabajos_b1 ORDER BY nota_id, version")


def decisiones(almacen):
    return consultar(almacen, "SELECT * FROM decisiones_b1 ORDER BY equipo")


def catalogo():
    definicion = definicion_planta()
    definicion["sensores"][0].update(alarma_baja=0.15, alarma_alta=1.5)
    definicion["sensores"].append({
        "tag": "B-101", "variable": "presion_descarga", "unidad": "kg/cm2",
        "alarma_baja": 95, "alarma_alta": 125,
    })
    return definicion


def confirmar(nota, partes, worker, *, identificador="FINAL"):
    r = respuesta(partes.obtener(nota.id), "Sí, confirmo.", identificador=identificador)
    procesar(partes, worker, r)
    return r


def test_confirmacion_crea_trabajo_con_referencias_y_copia_final(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    previo = consultar(partes, "SELECT * FROM outbox")
    r = confirmar(nota, partes, worker)
    (trabajo,) = trabajos(partes)
    assert trabajo["estado"] == "pendiente"
    assert (trabajo["nota_id"], trabajo["parte_id"], trabajo["version"]) == (
        nota.id, f"PARTE-{nota.id}", 1,
    )
    assert (trabajo["mensaje_id"], trabajo["respuesta_id"]) == (r.mensaje_id, r.id)
    assert json.loads(trabajo["filas"]) == [f.model_dump() for f in partes.obtener(nota.id).filas]
    assert resultado(partes, r.id) == "confirmada"
    assert decisiones(partes) == [] and consultar(partes, "SELECT * FROM outbox") == previo


@pytest.mark.parametrize("estado", ["borrador_pendiente", "rechazado", "revision_manual"])
def test_estados_sin_confirmacion_no_crean_trabajos(tmp_path, estado):
    texto = "La uno no está en 115 kilos de descarga" if estado == "revision_manual" else (
        "La uno está en 110 kilos de descarga"
    )
    nota, _, _, partes, worker = preparar_borrador(tmp_path, texto)
    if estado == "rechazado":
        procesar(partes, worker, respuesta(partes.obtener(nota.id), "Rechazo el parte."))
    assert partes.obtener(nota.id).estado == estado
    assert trabajos(partes) == []


def test_resolver_aclaracion_requiere_confirmar_el_borrador_nuevo(tmp_path):
    nota, _, partes, worker = iniciar(tmp_path, "El primario me dio dieciocho de diferencial")
    turno = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(turno, "Correcto, dale."))
    nuevo = partes.obtener(nota.id)
    assert nuevo.estado == "borrador_pendiente"
    assert trabajos(partes) == []
    confirmar(nota, partes, worker)
    assert [j["version"] for j in trabajos(partes)] == [nuevo.version]


def test_correccion_y_respuesta_obsoleta_solo_clasifican_version_confirmada(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(inicial, "No, la uno estaba en ciento treinta kilos."))
    procesar(partes, worker, respuesta(inicial, "Sí, confirmo.", identificador="VIEJA"))
    assert resultado(partes, "VIEJA") == "desfasada" and trabajos(partes) == []
    r = confirmar(nota, partes, worker)
    assert not partes.guardar_respuesta(r)
    procesar(partes, worker, r.model_copy(update={"id": "REPETIDA"}))
    assert [j["version"] for j in trabajos(partes)] == [2]
    assert WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta), catalogo).procesar_siguiente()
    (fila,) = decisiones(partes)
    assert fila["version"] == 2 and fila["resultado"] == "Escalado"
    assert json.loads(fila["evidencia"])[0]["valor"] == 130


def test_decisiones_por_equipo_con_evidencia_y_referencias(tmp_path):
    notas, _, _, partes, worker = preparar(tmp_path)
    foto = "/media/fotos/manometro.jpg"
    nota = crear_nota().model_copy(update={"fotos": [foto]})
    transcribir_nota(notas, nota, "Parte")
    filas = [
        FilaParte(tag="B-101", variable="presion_descarga_local", valor=110, unidad="kg/cm2"),
        FilaParte(tag="F-201A", variable="presion_diferencial", valor=1.8,
                 unidad="kg/cm2", observacion="Diferencial alto"),
        FilaParte(tag="TK-301", variable="nivel", valor=2.5, unidad="m"),
    ]
    assert partes.guardar(nota, "borrador_pendiente", filas, crear_borrador(nota, filas))
    confirmar(nota, partes, worker)

    procesador = WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta), catalogo)
    assert procesador.procesar_siguiente()
    assert not procesador.procesar_siguiente()
    assert trabajos(partes)[0]["estado"] == "clasificado"
    registros = {d["equipo"]: d for d in decisiones(partes)}
    assert {equipo: d["resultado"] for equipo, d in registros.items()} == {
        "B-101": "SinNovedad", "F-201A": "Escalado", "TK-301": "Indeterminado",
    }
    assert {d["nota_id"] for d in registros.values()} == {nota.id}
    filtro = json.loads(registros["F-201A"]["evidencia"])
    assert {e["motivo"] for e in filtro} == {"hallazgo_afirmado", "alarma_alta"}
    assert {(e["valor"], e["unidad"], e["observacion"]) for e in filtro} == {
        (1.8, "kg/cm2", "Diferencial alto"),
    }
    assert json.loads(consultar(partes, "SELECT payload FROM notas")[0]["payload"])["fotos"] == [foto]
    assert {r["topico"] for r in consultar(partes, "SELECT topico FROM outbox")} == {"sistema/tablet"}


def test_parte_por_voz_conserva_la_clausula_completa_de_reserva(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(
        tmp_path, "La uno está en 110 kilos de descarga. La tres está parada, en reserva.",
    )
    confirmar(nota, partes, worker)
    assert WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta), catalogo).procesar_siguiente()
    por_equipo = {d["equipo"]: d for d in decisiones(partes)}
    assert por_equipo["B-103"]["resultado"] == "SinNovedad"
    assert "en reserva" in json.loads(por_equipo["B-103"]["evidencia"])[0]["observacion"]


@pytest.mark.parametrize("accion", ["ABORT, 'fallo de prueba'", "IGNORE"])
def test_fallo_al_insertar_trabajo_revierte_confirmacion(tmp_path, accion):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, "Sí, confirmo.")
    assert partes.guardar_respuesta(r)
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute(f"""
            CREATE TRIGGER bloquear_trabajo BEFORE INSERT ON trabajos_b1
            BEGIN SELECT RAISE({accion}); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert partes.obtener(nota.id) == inicial
    assert resultado(partes, r.id) is None and trabajos(partes) == []
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("DROP TRIGGER bloquear_trabajo")
    assert worker.procesar_siguiente()
    assert partes.obtener(nota.id).estado == "confirmado" and len(trabajos(partes)) == 1


def test_fallo_durante_clasificacion_reintenta_tras_reinicio(tmp_path, monkeypatch):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    reloj = MagicMock(return_value=100)
    almacen = AlmacenClasificacionB1(partes.ruta, reloj=reloj)
    clasificar = part_classification_worker.clasificar_parte
    with monkeypatch.context() as m:
        m.setattr(part_classification_worker, "clasificar_parte", MagicMock(side_effect=RuntimeError("prueba")))
        assert WorkerClasificacionB1(almacen, catalogo).procesar_siguiente()
    assert part_classification_worker.clasificar_parte is clasificar
    (pendiente,) = trabajos(partes)
    assert pendiente["estado"] == "pendiente" and pendiente["intentos"] == 1
    assert "RuntimeError" in pendiente["ultimo_error"] and decisiones(partes) == []

    _, _, _, reabierto, _ = preparar(tmp_path)
    siguiente = WorkerClasificacionB1(AlmacenClasificacionB1(reabierto.ruta, reloj=reloj), catalogo)
    assert not siguiente.procesar_siguiente()
    reloj.return_value += 10
    assert siguiente.procesar_siguiente()
    (terminado,) = trabajos(reabierto)
    assert (terminado["nota_id"], terminado["version"], terminado["respuesta_id"]) == (
        pendiente["nota_id"], pendiente["version"], pendiente["respuesta_id"],
    )
    assert terminado["estado"] == "clasificado" and len(decisiones(reabierto)) == 1


def test_reclamo_interrumpido_y_trabajador_obsoleto_no_duplican_decisiones(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    reloj = MagicMock(return_value=100)
    almacen = AlmacenClasificacionB1(partes.ruta, reloj=reloj)
    anterior = almacen.reclamar()
    assert anterior is not None
    nuevo = AlmacenClasificacionB1(partes.ruta, reloj=reloj)
    procesador = WorkerClasificacionB1(nuevo, catalogo)
    assert not procesador.procesar_siguiente()
    reloj.return_value += 60
    vigente = nuevo.reclamar()
    assert vigente.nota_id == anterior.nota_id and vigente.version == anterior.version
    assert vigente.intento == anterior.intento + 1
    clasificacion = part_classification_worker.clasificar_parte(partes.obtener(nota.id).filas, catalogo())
    assert not almacen.completar(anterior, clasificacion)
    assert nuevo.completar(vigente, clasificacion)
    assert not nuevo.completar(vigente, clasificacion)
    assert not procesador.procesar_siguiente()
    assert len(decisiones(partes)) == 1


def test_catalogo_temporalmente_inaccesible_no_produce_normalidad(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    reloj = MagicMock(return_value=100)
    proveedor = MagicMock(side_effect=[httpx.ConnectError("sin catálogo"), catalogo()])
    procesador = WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta, reloj=reloj), proveedor)
    assert procesador.procesar_siguiente()
    assert decisiones(partes) == []
    assert not procesador.procesar_siguiente()
    reloj.return_value += 10
    assert procesador.procesar_siguiente()
    assert decisiones(partes)[0]["resultado"] == "SinNovedad"
    assert proveedor.call_count == 2


def test_catalogo_ausente_agota_reintentos_como_fallo_tecnico(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    reloj = MagicMock(return_value=100)
    proveedor = MagicMock(return_value={})
    procesador = WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta, reloj=reloj), proveedor)
    for _ in range(3):
        assert procesador.procesar_siguiente()
        reloj.return_value += 10
    assert not procesador.procesar_siguiente()
    assert proveedor.call_count == 3 and decisiones(partes) == []
    assert trabajos(partes)[0]["estado"] == "fallido"


def test_cliente_b1_reintenta_catalogo_incompleto_sin_cambiar_dialogo(monkeypatch):
    planta = ClientePlanta("http://sim:8100")
    incompleto = {"equipos": catalogo()["equipos"]}
    request = httpx.Request("GET", "http://sim:8100/api/planta")
    get = MagicMock(side_effect=[
        httpx.Response(200, json=incompleto, request=request),
        httpx.Response(200, json=incompleto, request=request),
        httpx.Response(200, json=catalogo(), request=request),
    ])
    monkeypatch.setattr(httpx, "get", get)
    assert planta.catalogo() == incompleto
    with pytest.raises(ValueError):
        planta.catalogo(requerir_sensores=True)
    assert planta.catalogo() == incompleto
    assert planta.catalogo(requerir_sensores=True) == catalogo()
    assert planta.catalogo(requerir_sensores=True) == catalogo()
    assert get.call_count == 3


def test_catalogo_bloqueado_permite_alerta_de_seguridad(tmp_path):
    nota, seguridad, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    consultando, liberar = Event(), Event()

    def proveedor():
        consultando.set()
        assert liberar.wait(timeout=5)
        return catalogo()

    procesador = WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta), proveedor)
    with ThreadPoolExecutor(max_workers=1) as pool:
        tarea = pool.submit(procesador.procesar_siguiente)
        try:
            assert consultando.wait(timeout=5)
            notas = preparar(tmp_path)[0]
            insegura = nota.model_copy(update={"id": "NOTA-SEG"})
            transcribir_nota(notas, insegura, "Sale humo de la bomba")
            assert seguridad.reclamar()["id"] == "SEG-NOTA-SEG-S3"
            assert seguridad.reclamar()["id"] == "TABLET-SEG-NOTA-SEG"
        finally:
            liberar.set()
        assert tarea.result(timeout=5)
    assert trabajos(partes)[0]["estado"] == "clasificado"


def test_fallo_al_guardar_segunda_decision_revierte_el_lote(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(
        tmp_path, "La uno está en 110 kilos de descarga. El primario dio uno coma ocho de diferencial.",
    )
    confirmar(nota, partes, worker)
    reloj = MagicMock(return_value=100)
    procesador = WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta, reloj=reloj), catalogo)
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("""
            CREATE TRIGGER bloquear_filtro BEFORE INSERT ON decisiones_b1
            WHEN NEW.equipo = 'F-201A'
            BEGIN SELECT RAISE(IGNORE); END
        """)
    assert procesador.procesar_siguiente()
    assert decisiones(partes) == [] and trabajos(partes)[0]["estado"] == "pendiente"
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("DROP TRIGGER bloquear_filtro")
    reloj.return_value += 10
    assert procesador.procesar_siguiente()
    assert [d["equipo"] for d in decisiones(partes)] == ["B-101", "F-201A"]


def test_entrypoint_procesa_trabajo_con_catalogo_local(tmp_path, monkeypatch):
    from sistema import security_integrated

    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    monkeypatch.setenv("PIA3_DB_PATH", str(partes.ruta))
    monkeypatch.setenv("PIA3_ENABLE_OUTBOX", "0")
    monkeypatch.setattr(security_integrated, "conectar_mqtt", MagicMock())
    monkeypatch.setattr(security_integrated, "ejecutar_heartbeat", MagicMock())
    monkeypatch.setattr(security_integrated, "WhisperLocal", MagicMock())
    monkeypatch.setattr(security_integrated.WorkerPersistente, "iniciar", lambda self: None)
    monkeypatch.setattr(security_integrated.WorkerPartes, "iniciar", lambda self: None)
    proveedor = MagicMock(return_value=catalogo())
    monkeypatch.setattr(ClientePlanta, "catalogo", proveedor)

    def procesar_y_salir(worker_b1):
        assert worker_b1.procesar_siguiente()
        raise KeyboardInterrupt

    monkeypatch.setattr(WorkerClasificacionB1, "iniciar", procesar_y_salir)
    security_integrated.main()
    proveedor.assert_called_once_with(requerir_sensores=True)
    assert decisiones(partes)[0]["resultado"] == "SinNovedad"


def test_migracion_aditiva_preserva_datos_y_no_reprocesa_confirmados(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    confirmar(nota, partes, worker)
    existentes = ("notas", "partes", "borradores_parte", "respuestas_parte", "outbox")
    antes = {tabla: consultar(partes, f"SELECT * FROM {tabla}") for tabla in existentes}
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("DROP TABLE decisiones_b1")
        db.execute("DROP TABLE trabajos_b1")
    _, _, _, reabierto, _ = preparar(tmp_path)
    assert reabierto.obtener(nota.id).estado == "confirmado"
    assert {tabla: consultar(reabierto, f"SELECT * FROM {tabla}") for tabla in existentes} == antes
    assert trabajos(reabierto) == [] and decisiones(reabierto) == []
    assert not WorkerClasificacionB1(AlmacenClasificacionB1(reabierto.ruta), catalogo).procesar_siguiente()
