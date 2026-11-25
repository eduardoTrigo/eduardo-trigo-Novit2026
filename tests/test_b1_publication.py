import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from datetime import timedelta, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from planta_contrato import EstadoSistema, EventoSistema, FilaParte, Topicos

from sistema.b1_event_worker import WorkerEventosB1
from sistema.b1_evidence import EnriquecedorB1
from sistema.b1_event_store import AlmacenEventosB1
from sistema.mqtt_client import publicar_estado
from sistema.outbox_publisher import publicar_siguiente
from sistema.part_classification_store import AlmacenClasificacionB1
from sistema.part_classification_worker import WorkerClasificacionB1
from sistema.part_context import ClientePlanta
from sistema.part_messages import crear_borrador
from sistema.security_outbox import OutboxSeguridad
from sistema.sensor_detection import avanzar, evaluar
from sistema.sensor_store import AlmacenSensoresB1
from test_part_classification_integration import catalogo, confirmar, consultar
from test_part_messages import preparar, transcribir_nota
from test_security_messages import crear_nota
from test_sensor_detection import CLAVE, LIMITES, lectura, ts
from test_sensor_store import episodios, persistir


FOTO = "/media/fotos/manometro/01.jpg"


def entorno(tmp_path):
    notas, seguridad, tablet, partes, worker = preparar(tmp_path)
    sensores = AlmacenSensoresB1(notas.ruta, "ensayo")
    salida = OutboxSeguridad(notas.ruta, b1=True)
    eventos = AlmacenEventosB1(salida)
    return notas, seguridad, tablet, partes, worker, sensores, salida, eventos


def clasificar(notas, partes, worker, filas, fotos=(), *, confirmar_parte=True):
    nota = crear_nota().model_copy(update={"fotos": list(fotos)})
    transcribir_nota(notas, nota, "Parte de prueba")
    assert partes.guardar(nota, "borrador_pendiente", filas, crear_borrador(nota, filas))
    if confirmar_parte:
        confirmar(nota, partes, worker)
        almacen = AlmacenClasificacionB1(notas.ruta)
        assert WorkerClasificacionB1(almacen, catalogo).procesar_siguiente()
    return nota


def descarga(valor):
    return FilaParte(tag="B-101", variable="presion_descarga_local", unidad="kg/cm2", valor=valor)


def pendiente(salida):
    return consultar(salida, "SELECT id, topico, payload FROM outbox WHERE es_b1 = 1 ORDER BY rowid")


def broker(*, ack=True):
    cliente = MagicMock()
    cliente.conectado.is_set.return_value = True
    cliente.publish.return_value.rc = 0
    cliente.publish.return_value.is_published.return_value = ack
    return cliente


def test_partes_publicables_por_equipo_con_contrato_y_evidencia(tmp_path):
    notas, _, tablet, partes, worker, _, salida, eventos = entorno(tmp_path)
    filtro = FilaParte(tag="F-201A", variable="presion_diferencial", valor=1.8, unidad="kg/cm2")
    tanque = FilaParte(tag="TK-301", variable="nivel", valor=2.5, unidad="m")
    nota = clasificar(notas, partes, worker, [descarga(110), filtro, tanque], [FOTO])
    assert eventos.encolar_siguiente() and eventos.encolar_siguiente()
    assert not eventos.encolar_siguiente()
    mensajes = [EventoSistema.model_validate_json(m["payload"], strict=True) for m in pendiente(salida)]
    assert {m.equipo: m.tipo.value for m in mensajes} == {"B-101": "SinNovedad", "F-201A": "Escalado"}
    assert all(m.ts == nota.ts and m.origen == "parte" and m.parte_id == f"PARTE-{nota.id}" for m in mensajes)
    assert all(m.nota_ids == [nota.id] for m in mensajes)
    desvio = next(m for m in mensajes if m.equipo == "F-201A")
    detalle = eventos.evidencia(desvio.id)
    assert detalle["filas"][0]["valor"] == 1.8 and detalle["reglas"][0]["alarma_alta"] == 1.5
    assert detalle["huella_catalogo"]
    assert detalle["fotos"][0]["ruta"] == FOTO and detalle["fotos"][0]["asociacion"] == "nota"
    assert tablet.reclamar()["topico"] == Topicos.TABLET
    assert AlmacenEventosB1(salida).encolar_siguiente() is False


def test_worker_encolado_reintenta_decision_duradera_tras_bloqueo_sqlite(tmp_path, monkeypatch):
    notas, _, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(110)])
    bloqueado, encolado = Event(), Event()
    conectar = sqlite3.connect
    leer, encolar = eventos._parte, eventos.encolar_siguiente
    enriquecedor = MagicMock()
    enriquecedor.procesar_siguiente.return_value = False
    worker_eventos = WorkerEventosB1(eventos, enriquecedor)

    def conectar_sin_espera(*args, **kwargs):
        return conectar(*args, **({"timeout": 0} | kwargs))

    def observar_lectura(db):
        try:
            return leer(db)
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
            bloqueado.set()
            raise

    def observar_encolado():
        resultado = encolar()
        if resultado:
            encolado.set()
        return resultado

    monkeypatch.setattr(sqlite3, "connect", conectar_sin_espera)
    monkeypatch.setattr(eventos, "_parte", observar_lectura)
    monkeypatch.setattr(eventos, "encolar_siguiente", observar_encolado)
    try:
        with closing(conectar(salida.ruta)) as lock, lock:
            lock.execute("BEGIN EXCLUSIVE")
            worker_eventos.iniciar()
            assert bloqueado.wait(timeout=3)
        assert encolado.wait(timeout=5)
        assert all(hilo.is_alive() for hilo in worker_eventos.hilos)
    finally:
        assert worker_eventos.detener()

    (registro,) = pendiente(salida)
    assert eventos.evidencia(registro["id"])["equipo"] == "B-101"
    assert not encolar()


def test_parte_pendiente_y_pico_no_encolan_eventos(tmp_path):
    notas, _, _, partes, worker, sensores, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(130)], confirmar_parte=False)
    for t, v in ((0, 110), (60, 130), (120, 110)):
        persistir(sensores, t, v)
    assert not eventos.encolar_siguiente() and pendiente(salida) == []


def test_sensor_reiniciado_sin_origen_y_nuevo_episodio(tmp_path):
    _, _, _, _, _, sensores, salida, _ = entorno(tmp_path)
    for t in (0, 60, 120):
        persistir(sensores, t)
    (abierto,) = episodios(sensores)
    eventos = AlmacenEventosB1(OutboxSeguridad(salida.ruta, b1=True))
    assert eventos.encolar_siguiente()
    (registro,) = pendiente(salida)
    evento = EventoSistema.model_validate_json(registro["payload"], strict=True)
    assert (evento.id, evento.tipo.value, evento.ts) == (abierto["id"], "Escalado", ts(120))
    assert evento.origen == "sensor" and evento.parte_id is None and evento.nota_ids == []
    assert eventos.evidencia(evento.id)["episodio"]["direccion"] == "alta"
    persistir(sensores, 180)
    assert not eventos.encolar_siguiente()
    for t, v in ((240, 110), (300, 110), (360, 110), (420, 130), (480, 130), (540, 130)):
        persistir(sensores, t, v)
    assert eventos.encolar_siguiente()
    assert len({m["id"] for m in pendiente(salida)}) == 2


@pytest.mark.parametrize("origen", ["parte", "sensor"])
@pytest.mark.parametrize("tabla,condicion", [("outbox", "WHEN NEW.es_b1 = 1"), ("eventos_b1", "")])
def test_rollback_completo_y_recuperacion_antes_de_encolar(tmp_path, origen, tabla, condicion):
    notas, _, _, partes, worker, sensores, salida, eventos = entorno(tmp_path)
    if origen == "parte":
        clasificar(notas, partes, worker, [descarga(130)])
    else:
        for t in (0, 60, 120):
            persistir(sensores, t)
    with closing(sqlite3.connect(salida.ruta)) as db, db:
        db.execute(f"""
            CREATE TRIGGER impedir_b1 BEFORE INSERT ON {tabla} {condicion}
            BEGIN SELECT RAISE(IGNORE); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        eventos.encolar_siguiente()
    assert pendiente(salida) == [] and consultar(salida, "SELECT id FROM eventos_b1") == []
    if origen == "sensor":
        assert episodios(sensores)[0]["publicacion"] == "pendiente"
    with closing(sqlite3.connect(salida.ruta)) as db, db:
        db.execute("DROP TRIGGER impedir_b1")
    reabierto = AlmacenEventosB1(OutboxSeguridad(salida.ruta, b1=True))
    assert reabierto.encolar_siguiente()
    assert reabierto.estado_publicacion(pendiente(salida)[0]["id"]) == "encolado"


@pytest.mark.parametrize("etapa", ["encolado", "antes_ack", "despues_ack"])
def test_reinicio_y_reenvio_con_identidad_y_payload_originales(tmp_path, etapa):
    notas, _, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(130)])
    assert eventos.encolar_siguiente()
    (original,) = pendiente(salida)
    cliente = broker()
    if etapa != "encolado":
        assert salida.reclamar() == original
    if etapa == "despues_ack":
        acuse = cliente.publish(topic=original["topico"], payload=original["payload"], qos=1)
        acuse.wait_for_publish(timeout=5)
        assert acuse.is_published()
    reabierta = OutboxSeguridad(salida.ruta, b1=True)
    monitor = AlmacenEventosB1(reabierta)
    assert monitor.estado_publicacion(original["id"]) == "encolado"
    assert reabierta.recuperar() == (etapa != "encolado")
    assert not monitor.encolar_siguiente()
    assert publicar_siguiente(reabierta, cliente)
    assert monitor.estado_publicacion(original["id"]) == "publicado"
    assert all(c.kwargs == {"topic": Topicos.EVENTOS, "payload": original["payload"], "qos": 1}
               for c in cliente.publish.call_args_list)


def test_timeout_y_carriles_exclusivos_preservan_reintento(tmp_path):
    notas, seguridad, tablet, partes, worker, _, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(130)])
    assert eventos.encolar_siguiente()
    assert seguridad.guardar("SEG-1", Topicos.EVENTOS, "{}")
    assert not publicar_siguiente(salida, broker(ack=False))
    (original,) = pendiente(salida)
    assert eventos.estado_publicacion(original["id"]) == "fallo_tecnico"
    assert seguridad.reclamar()["id"] == "SEG-1"
    assert tablet.reclamar()["topico"] == Topicos.TABLET
    assert not seguridad.confirmar(original["id"])
    assert salida.recuperar() == 0
    with closing(sqlite3.connect(salida.ruta)) as db, db:
        db.execute("UPDATE outbox SET proximo_intento = 0 WHERE id = ?", (original["id"],))
    assert publicar_siguiente(salida, broker())
    assert pendiente(salida) == [original]
    assert eventos.estado_publicacion(original["id"]) == "publicado"


def test_agotamiento_b1_reactivable_sin_cambiar_payload(tmp_path):
    notas, _, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(130)])
    assert eventos.encolar_siguiente()
    (original,) = pendiente(salida)
    cliente = broker(ack=False)
    for _ in range(salida.MAX_INTENTOS):
        assert not publicar_siguiente(salida, cliente)
        with closing(sqlite3.connect(salida.ruta)) as db, db:
            db.execute("UPDATE outbox SET proximo_intento = 0 WHERE id = ?", (original["id"],))
    assert salida.agotadas() == 1 and eventos.estado_publicacion(original["id"]) == "fallo_tecnico"
    assert not publicar_siguiente(salida, cliente) and cliente.publish.call_count == salida.MAX_INTENTOS
    assert salida.reactivar(original["id"])
    assert publicar_siguiente(salida, broker())
    assert pendiente(salida) == [original]


def test_migracion_preserva_filas_historicas_y_sus_carriles(tmp_path):
    ruta = tmp_path / "historico.sqlite"
    with closing(sqlite3.connect(ruta)) as db, db:
        db.executescript("""
            CREATE TABLE outbox (id TEXT PRIMARY KEY, topico TEXT, payload TEXT,
                estado TEXT, intentos INTEGER, ultimo_error TEXT, actualizado TEXT,
                proximo_intento REAL, es_borrador INTEGER);
            INSERT INTO outbox VALUES ('seg', 'sistema/eventos', '{}', 'pendiente', 1, 'error', '', 0, 0);
            INSERT INTO outbox VALUES ('tab', 'sistema/tablet', '{}', 'pendiente', 0, NULL, '', 0, 1);
        """)
    assert OutboxSeguridad(ruta, b1=True).reclamar() is None
    assert OutboxSeguridad(ruta).reclamar()["id"] == "seg"
    assert OutboxSeguridad(ruta, borradores=True).reclamar()["id"] == "tab"


def test_enriquecimiento_reintenta_historico_y_conserva_payload(tmp_path):
    notas, _, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    nota = clasificar(notas, partes, worker, [descarga(130)], [FOTO, "http://otro/foto.jpg"])
    assert eventos.encolar_siguiente()
    (original,) = pendiente(salida)
    planta = MagicMock()
    planta.catalogo.return_value = catalogo()
    planta.historial.side_effect = httpx.ReadTimeout("historian caído")
    planta.foto_b1.return_value = ("image/jpeg", b"\xff\xd8\xffdatos")
    assert EnriquecedorB1(eventos, planta).procesar_siguiente()
    pendiente_evidencia = eventos.evidencia(original["id"])
    assert pendiente_evidencia["tendencias"][0]["estado"] == "inaccesible"
    assert [f["estado"] for f in pendiente_evidencia["fotos"]] == ["recuperada", "invalida"]
    planta.foto_b1.assert_called_once_with(FOTO)
    with closing(sqlite3.connect(salida.ruta)) as db, db:
        db.execute("UPDATE eventos_b1 SET proximo_contexto = 0 WHERE id = ?", (original["id"],))
    planta.historial.side_effect = None
    planta.historial.return_value = [{"ts": nota.ts.isoformat(), "valor": 130}]
    assert EnriquecedorB1(AlmacenEventosB1(salida), planta).procesar_siguiente()
    detalle = eventos.evidencia(original["id"])
    assert detalle["tendencias"][0]["estado"] == "incompleta"
    assert detalle["tendencias"][0]["muestras"][0]["valor"] == 130
    assert pendiente(salida) == [original]
    planta.foto_b1.assert_called_once()
    assert len(consultar(salida, "SELECT evento_id FROM fotos_evento_b1")) == 1


def test_ack_b1_esperando_permite_seguridad_con_el_mismo_cliente(tmp_path):
    notas, seguridad, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(130)])
    assert eventos.encolar_siguiente()
    assert seguridad.guardar("SEG-1", Topicos.EVENTOS, '{"seguridad":true}')
    cliente = broker()
    esperando, liberar = Event(), Event()
    ack_b1, ack_seg = MagicMock(rc=0), MagicMock(rc=0)
    ack_b1.is_published.return_value = ack_seg.is_published.return_value = True

    def esperar_ack(timeout):
        esperando.set()
        assert liberar.wait(timeout=5)

    ack_b1.wait_for_publish.side_effect = esperar_ack
    cliente.publish.side_effect = [ack_b1, ack_seg]
    with ThreadPoolExecutor(max_workers=1) as pool:
        futuro = pool.submit(publicar_siguiente, salida, cliente)
        try:
            assert esperando.wait(timeout=5)
            assert publicar_siguiente(seguridad, cliente)
            assert cliente.publish.call_count == 2
        finally:
            liberar.set()
        assert futuro.result(timeout=5)


def test_sensor_con_parte_contextual_tendencia_completa_y_foto_ausente(tmp_path):
    notas, _, _, partes, worker, sensores, salida, eventos = entorno(tmp_path)
    nota = clasificar(notas, partes, worker, [descarga(110)], [FOTO])
    for t in (1800, 1860, 1920):
        persistir(sensores, t)
    assert eventos.encolar_siguiente() and eventos.encolar_siguiente()
    evento = next(EventoSistema.model_validate_json(m["payload"], strict=True)
                  for m in pendiente(salida) if m["id"].startswith("B1-SENSOR"))
    planta = MagicMock()
    planta.catalogo.return_value = catalogo()
    planta.historial.side_effect = lambda tag, variable, hasta, desde: [
        {"ts": (desde + timedelta(minutes=m)).isoformat(), "valor": 130} for m in range(181)
    ]
    respuesta = httpx.Response(404, request=httpx.Request("GET", "http://sim:8100" + FOTO))
    with pytest.raises(httpx.HTTPStatusError) as error:
        respuesta.raise_for_status()
    planta.foto_b1.side_effect = error.value
    assert EnriquecedorB1(eventos, planta).procesar_siguiente()
    detalle = eventos.evidencia(evento.id)
    assert detalle["contexto_parte"]["nota_id"] == nota.id
    assert detalle["nota_ids"] == [] and evento.nota_ids == [] and evento.parte_id is None
    assert detalle["fotos"][0]["estado"] == "ausente"
    (tendencia,) = detalle["tendencias"]
    assert tendencia["estado"] == "disponible" and len(tendencia["muestras"]) == 181
    planta.historial.assert_called_once_with("B-101", "presion_descarga", evento.ts,
                                            desde=evento.ts - timedelta(hours=3))


def test_tendencia_incompleta_puede_completarse_mas_tarde(tmp_path):
    notas, _, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    nota = clasificar(notas, partes, worker, [descarga(130)])
    assert eventos.encolar_siguiente()
    (original,) = pendiente(salida)
    planta = MagicMock()
    planta.catalogo.return_value = {"sensores": [catalogo()["sensores"][-1]]}
    planta.historial.return_value = [{"ts": nota.ts.isoformat(), "valor": 130}]
    contexto = EnriquecedorB1(eventos, planta)
    assert contexto.procesar_siguiente()
    assert contexto.almacen.evidencia(original["id"])["tendencias"][0]["estado"] == "incompleta"
    with closing(sqlite3.connect(salida.ruta)) as db, db:
        db.execute("UPDATE eventos_b1 SET proximo_contexto = 0 WHERE id = ?", (original["id"],))
    planta.historial.side_effect = lambda tag, variable, hasta, desde: [
        {"ts": (desde + timedelta(minutes=m)).isoformat(), "valor": 130} for m in range(181)
    ]
    assert contexto.procesar_siguiente()
    assert contexto.almacen.evidencia(original["id"])["tendencias"][0]["estado"] == "disponible"
    assert pendiente(salida) == [original]


@pytest.mark.parametrize("ruta", [
    "http://otro/media/fotos/a.jpg", "//otro/media/fotos/a.jpg",
    "/media/fotos/../a.jpg", "/media/fotos/%2e%2e/a.jpg", "/media/fotos/a.jpg?x=1",
])
def test_foto_rechaza_referencias_externas_y_traversal_sin_consulta(monkeypatch, ruta):
    stream = MagicMock()
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError):
        ClientePlanta("http://sim:8100").foto_b1(ruta)
    stream.assert_not_called()


@pytest.mark.parametrize("tipo,tamano,cabecera", [
    ("text/html", None, b"<html>"),
    ("image/jpeg", "524289", b"\xff\xd8\xff"),
    ("image/jpeg", None, b"firma invalida"),
    ("image/jpeg", None, b"\xff\xd8\xff" + b"x" * (512 * 1024)),
])
def test_foto_acotada_por_tipo_firma_y_bytes(monkeypatch, tipo, tamano, cabecera):
    res = MagicMock(headers={"content-type": tipo})
    if tamano is not None:
        res.headers["content-length"] = tamano
    res.iter_bytes.return_value = iter([cabecera])
    stream = MagicMock(return_value=nullcontext(res))
    monkeypatch.setattr(httpx, "stream", stream)
    with pytest.raises(ValueError):
        ClientePlanta("http://sim:8100").foto_b1(FOTO)
    assert stream.call_args.kwargs["follow_redirects"] is False


def test_foto_valida_se_descarga_solo_desde_api_de_planta(monkeypatch):
    url = "http://sim:8100" + FOTO
    contenido = b"\xff\xd8\xffdatos"
    res = httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=contenido,
                         request=httpx.Request("GET", url))
    stream = MagicMock(return_value=nullcontext(res))
    monkeypatch.setattr(httpx, "stream", stream)
    assert ClientePlanta("http://sim:8100").foto_b1(FOTO) == ("image/jpeg", contenido)
    assert stream.call_args.args == ("GET", url)


def test_sensor_conserva_offset_del_historian_y_timestamp_del_evento(tmp_path):
    _, _, _, _, _, sensores, salida, eventos = entorno(tmp_path)
    offset = timezone(timedelta(hours=-3))
    for t in (0, 60, 120):
        muestra = lectura(t, 130).model_copy(update={"ts": ts(t).astimezone(offset)})
        anterior = sensores.leer(CLAVE)
        cambio = avanzar(anterior.serie, evaluar(muestra, LIMITES, None), epoca=sensores.epoca,
                         clave=CLAVE, huella=LIMITES.huella)
        assert sensores.guardar(CLAVE, anterior, cambio)
    assert eventos.encolar_siguiente()
    planta = MagicMock()
    planta.catalogo.return_value = catalogo()
    planta.historial.return_value = []
    assert EnriquecedorB1(eventos, planta).procesar_siguiente()
    esperado = ts(120).astimezone(offset)
    llamada = planta.historial.call_args
    assert llamada.args[-1].isoformat() == esperado.isoformat()
    assert llamada.kwargs["desde"].isoformat() == (esperado - timedelta(hours=3)).isoformat()
    evento = EventoSistema.model_validate_json(pendiente(salida)[0]["payload"], strict=True)
    assert evento.ts.isoformat() == ts(120).isoformat()


@pytest.mark.parametrize(("condicion", "texto"), [
    ("S1", "Hay un chorro de agua a presión"), ("S2", "Se siente olor a gas"),
    ("S3", "Sale humo de la bomba"), ("S4", "Hay una persona lesionada"),
])
def test_evidencia_bloqueada_permite_escalado_alerta_y_heartbeat(tmp_path, condicion, texto):
    notas, seguridad, _, partes, worker, sensores, salida, eventos = entorno(tmp_path)
    nota = clasificar(notas, partes, worker, [descarga(130)])
    assert eventos.encolar_siguiente()
    planta = MagicMock()
    planta.catalogo.return_value = catalogo()
    consultando, liberar = Event(), Event()

    def historial(*args, **kwargs):
        consultando.set()
        assert liberar.wait(timeout=5)
        return []

    planta.historial.side_effect = historial
    cliente = broker()
    with ThreadPoolExecutor(max_workers=1) as pool:
        futuro = pool.submit(EnriquecedorB1(eventos, planta).procesar_siguiente)
        try:
            assert consultando.wait(timeout=5)
            for t in (0, 60, 120):
                persistir(sensores, t)
            assert eventos.encolar_siguiente()
            assert publicar_siguiente(salida, cliente)
            insegura = nota.model_copy(update={"id": "NOTA-SEG"})
            transcribir_nota(notas, insegura, texto)
            assert publicar_siguiente(seguridad, cliente) and publicar_siguiente(seguridad, cliente)
            alerta = EventoSistema.model_validate_json(cliente.publish.call_args_list[-2].kwargs["payload"])
            assert alerta.id == f"SEG-NOTA-SEG-{condicion}"
            assert cliente.publish.call_args.kwargs["topic"] == Topicos.TABLET
            publicar_estado(cliente)
            EstadoSistema.model_validate_json(cliente.publish.call_args.kwargs["payload"])
            assert cliente.publish.call_args.kwargs["topic"] == Topicos.ESTADO
        finally:
            liberar.set()
        assert futuro.result(timeout=5)


@pytest.mark.parametrize("habilitado", ["0", "1"])
def test_entrypoint_respeta_flag_y_publica_por_carril_b1(tmp_path, monkeypatch, habilitado):
    from sistema import security_integrated

    notas, _, _, partes, worker, _, salida, eventos = entorno(tmp_path)
    clasificar(notas, partes, worker, [descarga(130)])
    monkeypatch.setenv("PIA3_DB_PATH", str(notas.ruta))
    monkeypatch.setenv("PIA3_ENABLE_OUTBOX", habilitado)
    cliente = broker()
    monkeypatch.setattr(security_integrated, "conectar_mqtt", MagicMock(return_value=cliente))
    monkeypatch.setattr(security_integrated, "WhisperLocal", MagicMock())
    hilos = SimpleNamespace(Event=MagicMock(), Thread=MagicMock())
    hilos.Event.return_value.wait.return_value = True
    monkeypatch.setattr(security_integrated, "threading", hilos)
    for tipo in (security_integrated.WorkerPersistente, security_integrated.WorkerPartes,
                 WorkerClasificacionB1, security_integrated.WorkerSensoresB1):
        monkeypatch.setattr(tipo, "iniciar", lambda self: None)
    monkeypatch.setattr(WorkerEventosB1, "iniciar", lambda self: self.almacen.encolar_siguiente())
    publicadores = []

    def iniciar(publicador):
        publicadores.append((publicador.outbox.borradores, publicador.outbox.b1))
        if publicador.outbox.b1:
            assert publicar_siguiente(publicador.outbox, publicador.cliente)

    monkeypatch.setattr(security_integrated.WorkerOutbox, "iniciar", iniciar)
    security_integrated.main()
    (original,) = pendiente(salida)
    if habilitado == "1":
        assert publicadores == [(False, False), (True, False), (False, True)]
        assert eventos.estado_publicacion(original["id"]) == "publicado"
    else:
        assert publicadores == [] and eventos.estado_publicacion(original["id"]) == "encolado"
        cliente.publish.assert_not_called()
