import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from planta_contrato import EstadoEnergia, EstadoSistema, Topicos

from sistema.mqtt_client import conectar_mqtt, publicar_estado
from sistema.note_store import AlmacenNotas
from sistema.part_classification_store import AlmacenClasificacionB1
from sistema.part_classification_worker import WorkerClasificacionB1
from sistema.part_context import ClientePlanta
from sistema.sensor_store import AlmacenSensoresB1
from sistema.sensor_worker import WorkerSensoresB1
from test_part_classification_integration import catalogo, confirmar, decisiones
from test_part_messages import transcribir_nota
from test_part_workflow import preparar_borrador
from test_sensor_detection import CLAVE, lectura, ts
from test_sensor_store import episodios


def preparar(tmp_path, *, capacidad=256, ruta=None):
    planta = MagicMock()
    planta.catalogo.return_value = catalogo()
    planta.historial.return_value = []
    planta.estado_operativo.return_value = {}
    almacen = AlmacenSensoresB1(ruta or tmp_path / "sensores.sqlite", "prueba")
    reloj = MagicMock(return_value=100.0)
    return planta, almacen, WorkerSensoresB1(almacen, planta, capacidad=capacidad, reloj=reloj), reloj


def mensaje(segundos, valor, *, clave=CLAVE, unidad="kg/cm2"):
    return SimpleNamespace(topic=Topicos.SENSORES.format(tag=clave[0], variable=clave[1]),
                           payload=lectura(segundos, valor, clave=clave, unidad=unidad).model_dump_json().encode())


def procesar(worker, *mensajes):
    for m in mensajes:
        assert worker.encolar(m.topic, m.payload)
        assert worker.procesar_siguiente()


def test_mqtt_resuscripcion_y_recepcion_liviana(tmp_path, monkeypatch):
    planta, almacen, worker, _ = preparar(tmp_path)
    broker = MagicMock()
    broker.subscribe.return_value = (0, 1)
    monkeypatch.setattr("sistema.mqtt_client.mqtt.Client", lambda **_: broker)
    notas, respuestas = MagicMock(), MagicMock()
    conectar_mqtt(encolar_nota=notas, encolar_respuesta=respuestas,
                  encolar_sensor=worker.encolar, discontinuidad_sensores=worker.marcar_discontinuidad)
    broker.on_connect(broker, None, None, 0, None)
    broker.on_message(broker, None, mensaje(0, 130))
    planta.catalogo.assert_not_called()
    assert almacen.leer(CLAVE).serie.ultimo is None
    assert worker.procesar_siguiente()
    assert almacen.leer(CLAVE).serie.ultimo.ts == ts(0)
    broker.on_disconnect(broker, None, None, 0, None)
    broker.on_connect(broker, None, None, 0, None)
    broker.on_message(broker, None, mensaje(60, 130))
    assert worker.procesar_siguiente()
    assert [p.ts for p in almacen.leer(CLAVE).serie.ventana] == [ts(60)]
    suscripciones = [(x.args[0], x.kwargs.get("qos", 0)) for x in broker.subscribe.call_args_list]
    assert suscripciones == [
        (Topicos.RECORRIDAS, 0), (Topicos.RESPUESTAS, 1), ("planta/sensores/#", 0), (Topicos.ENERGIA, 1),
    ] * 2
    notas.assert_not_called()
    respuestas.assert_not_called()


@pytest.mark.parametrize("defecto", ["json", "tópico", "ts", "NaN", "bool"])
def test_marco_invalido_interrumpe_la_serie_sin_normalizarlo_a_cero(tmp_path, defecto):
    _, almacen, worker, _ = preparar(tmp_path)
    procesar(worker, mensaje(0, 130), mensaje(60, 130))
    frame = mensaje(90, 130)
    if defecto == "json":
        frame.payload = b'{"desconocido":true}'
    elif defecto == "tópico":
        frame.payload = mensaje(90, 130, clave=("B-102", CLAVE[1])).payload
    elif defecto == "ts":
        frame.payload = lectura(90, 130).model_copy(update={"ts": ts(90).replace(tzinfo=None)}).model_dump_json().encode()
    else:
        frame.payload = frame.payload.replace(b"130.0", b"NaN" if defecto == "NaN" else b"true")
    procesar(worker, frame, mensaje(120, 130))
    assert episodios(almacen) == []
    assert [p.ts for p in almacen.leer(CLAVE).serie.ventana] == [ts(120)]


def test_saturacion_descarta_el_lote_anterior_y_reancla_sin_bloqueo(tmp_path):
    _, almacen, worker, _ = preparar(tmp_path, capacidad=1)
    procesar(worker, mensaje(0, 130))
    en_cola = mensaje(60, 130)
    assert worker.encolar(en_cola.topic, en_cola.payload)
    assert not worker.encolar(en_cola.topic, en_cola.payload)
    assert worker.perdidas == 1
    assert worker.procesar_siguiente()
    procesar(worker, mensaje(120, 130), mensaje(180, 130))
    assert episodios(almacen) == []
    procesar(worker, mensaje(240, 130))
    assert episodios(almacen)[0]["inicio"] == ts(120).isoformat()


def test_discontinuidad_al_extraer_conserva_primera_muestra_de_la_generacion(tmp_path, monkeypatch):
    _, almacen, worker, _ = preparar(tmp_path)
    procesar(worker, mensaje(0, 130))
    obtener = worker.cola.get_nowait

    def desconectar_y_recibir():
        worker.marcar_discontinuidad()
        frame = mensaje(60, 130)
        assert worker.encolar(frame.topic, frame.payload)
        return obtener()

    with monkeypatch.context() as m:
        m.setattr(worker.cola, "get_nowait", desconectar_y_recibir)
        assert worker.procesar_siguiente()
    assert [p.ts for p in almacen.leer(CLAVE).serie.ventana] == [ts(60)]
    procesar(worker, mensaje(120, 130), mensaje(180, 130))
    assert episodios(almacen)[0]["inicio"] == ts(60).isoformat()


@pytest.mark.parametrize("discontinuidad", [False, True])
def test_worker_sensores_recupera_lectura_tras_bloqueo_sqlite(tmp_path, monkeypatch, discontinuidad):
    _, almacen, worker, _ = preparar(tmp_path)
    procesar(worker, mensaje(0, 130), mensaje(60, 130))
    bloqueada, siguiente_guardada = Event(), Event()
    conectar = sqlite3.connect
    leer, guardar = almacen.leer, almacen.guardar

    def conectar_sin_espera(*args, **kwargs):
        return conectar(*args, **({"timeout": 0} | kwargs))

    def observar_lectura(clave):
        try:
            return leer(clave)
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
            bloqueada.set()
            raise

    def observar_guardado(clave, anterior, cambio):
        resultado = guardar(clave, anterior, cambio)
        if cambio.serie.ultimo.ts == ts(180):
            siguiente_guardada.set()
        return resultado

    monkeypatch.setattr(sqlite3, "connect", conectar_sin_espera)
    monkeypatch.setattr(almacen, "leer", observar_lectura)
    monkeypatch.setattr(almacen, "guardar", observar_guardado)
    ultima_fuera_de_limite, siguiente = mensaje(120, 130), mensaje(180, 130)
    try:
        with closing(conectar(almacen.ruta)) as lock, lock:
            lock.execute("BEGIN EXCLUSIVE")
            assert worker.encolar(ultima_fuera_de_limite.topic, ultima_fuera_de_limite.payload)
            worker.iniciar()
            assert bloqueada.wait(timeout=3)
            if discontinuidad:
                worker.marcar_discontinuidad()
        assert worker.encolar(siguiente.topic, siguiente.payload)
        assert siguiente_guardada.wait(timeout=4)
        assert worker.hilo.is_alive()
    finally:
        assert worker.detener()

    registros = episodios(almacen)
    if discontinuidad:
        assert registros == []
        assert [p.ts for p in leer(CLAVE).serie.ventana] == [ts(180)]
    else:
        assert [(e["inicio"], e["abierto"]) for e in registros] == [(ts(0).isoformat(), ts(120).isoformat())]


def test_independencia_de_equipos_y_variables(tmp_path):
    planta, almacen, worker, _ = preparar(tmp_path)
    datos = planta.catalogo.return_value
    segundo = ("B-102", CLAVE[1])
    vibracion = (CLAVE[0], "vibracion")
    datos["sensores"].extend([
        {"tag": segundo[0], "variable": segundo[1], "unidad": "kg/cm2", "alarma_alta": 125},
        {"tag": vibracion[0], "variable": vibracion[1], "unidad": "mm/s", "alarma_alta": 7.1},
    ])
    for t in (0, 60, 120):
        procesar(worker, mensaje(t, 130), mensaje(t, 110, clave=segundo),
                  mensaje(t, 8 if t == 60 else 2, clave=vibracion, unidad="mm/s"))
    assert [(e["equipo"], e["variable"]) for e in episodios(almacen)] == [CLAVE]
    planta.estado_operativo.assert_not_called()


def test_catalogo_inaccesible_reintento_acotado_y_historian_no_disponible(tmp_path):
    planta, almacen, worker, reloj = preparar(tmp_path)
    datos = planta.catalogo.return_value
    planta.catalogo.side_effect = [httpx.ConnectError("catálogo caído"), datos]
    procesar(worker, mensaje(0, 130), mensaje(60, 130))
    assert planta.catalogo.call_count == 1
    reloj.return_value += 3
    procesar(worker, mensaje(120, 130))
    planta.historial.side_effect = httpx.ReadTimeout("histórico caído")
    procesar(worker, mensaje(360, 130), mensaje(600, 130))
    assert planta.historial.call_count == 1
    assert episodios(almacen) == []
    assert almacen.leer(CLAVE).serie.motivo == "hueco_sin_cobertura"


def test_historian_cubre_hueco_solo_tras_una_muestra_en_vivo(tmp_path):
    planta, almacen, worker, _ = preparar(tmp_path)
    planta.historial.return_value = [{"ts": ts(t).isoformat(), "valor": 130} for t in (60, 120, 180)]
    procesar(worker, mensaje(0, 130))
    planta.historial.assert_not_called()
    procesar(worker, mensaje(180, 130))
    planta.historial.assert_called_once_with(*CLAVE, ts(180), desde=ts(0))
    assert episodios(almacen)[0]["publicacion"] == "pendiente"
    procesar(worker, mensaje(180, 130), mensaje(240, 130))
    assert len(episodios(almacen)) == 1


def test_estado_contemporaneo_cache_y_reserva_que_entra_en_marcha(tmp_path):
    planta, almacen, worker, reloj = preparar(tmp_path)
    for t, estado in [(0, "parada"), (60, "marcha"), (120, "marcha"), (180, "marcha")]:
        planta.estado_operativo.return_value = {
            "ts": ts(t + 1).isoformat(), "hay_energia": True,
            "bombas": [{"tag": CLAVE[0], "estado": estado}],
        }
        procesar(worker, mensaje(t, 90), mensaje(t, 90))
        reloj.return_value += 1
    assert planta.estado_operativo.call_count == 4
    assert episodios(almacen)[0]["inicio"] == ts(60).isoformat()


def test_estado_actual_no_se_aplica_retroactivamente(tmp_path):
    planta, almacen, worker, _ = preparar(tmp_path)
    planta.estado_operativo.return_value = {
        "ts": ts(300).isoformat(), "hay_energia": True,
        "bombas": [{"tag": CLAVE[0], "estado": "marcha"}],
    }
    procesar(worker, mensaje(0, 90), mensaje(60, 90), mensaje(120, 90))
    assert episodios(almacen) == []
    assert almacen.leer(CLAVE).serie.motivo == "estado_operativo_no_corroborado"


def test_estado_operativo_malformado_se_trata_como_desconocido(tmp_path):
    planta, almacen, worker, _ = preparar(tmp_path)
    planta.estado_operativo.return_value = {"ts": ts(0).isoformat(), "bombas": [None]}
    procesar(worker, mensaje(0, 90))
    assert almacen.leer(CLAVE).serie.motivo == "estado_operativo_no_corroborado"


def test_filtro_en_mantenimiento_no_escala_por_diferencial_bajo(tmp_path):
    planta, almacen, worker, reloj = preparar(tmp_path)
    filtro = ("F-201A", "presion_diferencial")
    for t in (0, 60, 120):
        planta.estado_operativo.return_value = {
            "ts": ts(t).isoformat(), "filtros": [{"tag": filtro[0], "en_cambio": True}],
        }
        procesar(worker, mensaje(t, 0.05, clave=filtro))
        reloj.return_value += 1
    assert episodios(almacen) == []
    assert almacen.leer(filtro).serie.motivo == "parada_corroborada"


def test_cambio_de_energia_interrumpe_sin_cerrar_episodio(tmp_path):
    _, almacen, worker, _ = preparar(tmp_path)

    def energia(t, hay):
        return SimpleNamespace(topic=Topicos.ENERGIA, payload=EstadoEnergia(
            ts=ts(t), hay_energia=hay,
        ).model_dump_json().encode())

    procesar(worker, mensaje(0, 130), mensaje(60, 130), energia(70, False),
              mensaje(120, 130), energia(130, True), mensaje(180, 130))
    assert episodios(almacen) == []
    procesar(worker, mensaje(240, 130), mensaje(300, 130), energia(310, False))
    assert almacen.leer(CLAVE).serie.activo.id == episodios(almacen)[0]["id"]


@pytest.mark.parametrize(("condicion", "texto"), [
    ("S1", "Hay un chorro de agua a presión"),
    ("S2", "Se siente olor a gas"),
    ("S3", "Sale humo de la bomba"),
    ("S4", "Hay una persona lesionada"),
])
def test_historian_bloqueado_no_impide_partes_seguridad_y_heartbeat(tmp_path, condicion, texto):
    nota, seguridad, _, partes, worker_partes = preparar_borrador(tmp_path)
    _, almacen, sensores, _ = preparar(tmp_path, ruta=partes.ruta)
    procesar(sensores, mensaje(0, 130))
    consultando, liberar = Event(), Event()

    def historial(*args, **kwargs):
        consultando.set()
        assert liberar.wait(timeout=6)
        return [{"ts": ts(t).isoformat(), "valor": 130} for t in (60, 120)]

    sensores.planta.historial.side_effect = historial
    tardia = mensaje(180, 130)
    assert sensores.encolar(tardia.topic, tardia.payload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pendiente = pool.submit(sensores.procesar_siguiente)
        try:
            assert consultando.wait(timeout=5)
            confirmar(nota, partes, worker_partes)
            assert WorkerClasificacionB1(AlmacenClasificacionB1(partes.ruta), catalogo).procesar_siguiente()
            assert decisiones(partes)[0]["resultado"] == "SinNovedad"
            insegura = nota.model_copy(update={"id": "NOTA-SEG"})
            transcribir_nota(AlmacenNotas(partes.ruta), insegura, texto)
            assert seguridad.reclamar()["id"] == f"SEG-NOTA-SEG-{condicion}"
            assert seguridad.reclamar()["id"] == "TABLET-SEG-NOTA-SEG"
            broker = MagicMock()
            broker.publish.return_value.rc = 0
            publicar_estado(broker)
            assert broker.publish.call_args.kwargs["topic"] == Topicos.ESTADO
            EstadoSistema.model_validate_json(broker.publish.call_args.kwargs["payload"])
        finally:
            liberar.set()
        assert pendiente.result(timeout=6)
    assert len(episodios(almacen)) == 1


def test_cliente_historial_acotado_usa_parametros_y_timeout(monkeypatch):
    planta = ClientePlanta("http://sim:8100")
    get = MagicMock(return_value=MagicMock(json=MagicMock(return_value=[])))
    monkeypatch.setattr(httpx, "get", get)
    assert planta.historial(*CLAVE, ts(180), desde=ts(0)) == []
    get.assert_called_once_with(
        "http://sim:8100/api/historian/B-101/presion_descarga",
        params={"desde": ts(0).isoformat(), "hasta": ts(180).isoformat()}, timeout=2.0,
    )


def test_entrypoint_cablea_sensores_y_conserva_decision_pendiente(tmp_path, monkeypatch):
    from sistema import security_integrated

    ruta = tmp_path / "runtime.sqlite"
    monkeypatch.setenv("PIA3_DB_PATH", str(ruta))
    monkeypatch.setenv("PIA3_SENSOR_EPOCH", "ensayo")
    monkeypatch.setenv("PIA3_ENABLE_OUTBOX", "0")
    monkeypatch.setattr(security_integrated, "WhisperLocal", MagicMock())
    monkeypatch.setattr(security_integrated, "ejecutar_heartbeat", MagicMock())
    for worker in (security_integrated.WorkerPersistente, security_integrated.WorkerPartes,
                   security_integrated.WorkerClasificacionB1):
        monkeypatch.setattr(worker, "iniciar", lambda self: None)
    monkeypatch.setattr(ClientePlanta, "catalogo", MagicMock(return_value=catalogo()))
    cliente = MagicMock()

    def conectar(*, encolar_nota, encolar_respuesta, encolar_sensor, discontinuidad_sensores):
        for t in (0, 60, 120):
            frame = mensaje(t, 130)
            assert encolar_sensor(frame.topic, frame.payload)
        return cliente

    def procesar_y_salir(worker):
        assert [worker.procesar_siguiente() for _ in range(3)] == [True] * 3
        raise KeyboardInterrupt

    monkeypatch.setattr(security_integrated, "conectar_mqtt", conectar)
    monkeypatch.setattr(WorkerSensoresB1, "iniciar", procesar_y_salir)
    security_integrated.main()
    (apertura,) = episodios(AlmacenSensoresB1(ruta, "ensayo"))
    assert (apertura["epoca"], apertura["publicacion"]) == ("ensayo", "pendiente")
    cliente.publish.assert_not_called()
    cliente.loop_stop.assert_called_once()
