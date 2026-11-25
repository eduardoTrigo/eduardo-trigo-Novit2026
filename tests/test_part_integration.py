import json
from threading import Event
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import paho.mqtt.client as mqtt
import pytest
from planta_contrato import EstadoSistema, EventoSistema, MensajeTablet, Topicos

from sistema.mqtt_client import conectar_mqtt
from sistema.outbox_publisher import publicar_siguiente
from sistema.part_context import ClientePlanta
from sistema.part_worker import WorkerPartes
from sistema.persistent_ingress import crear_entrada_persistente
from sistema.security_integrated import ejecutar_heartbeat
from sistema.security_persistent_processor import crear_procesador_persistente
from test_part_clarification import catalogo
from test_part_messages import preparar
from test_part_workflow import respuesta, resultado
from test_security_messages import crear_nota
from test_sensor_mqtt import mensaje as mensaje_sensor
from test_sensor_mqtt import preparar as preparar_sensores
from test_transcription_service import crear_wav_prueba


@pytest.mark.parametrize("condicion,frase", [
    ("S1", "Hay un chorro de agua a presión."),
    ("S2", "Se siente olor a gas."),
    ("S3", "Sale humo de la bomba."),
    ("S4", "Hay un operador herido."),
])
def test_mqtt_wav_seguridad_y_parte_confirmado_tras_reinicio(tmp_path, monkeypatch, condicion, frase):
    notas, alertas, borradores, partes, _ = preparar(tmp_path)
    broker = MagicMock()
    broker.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    broker.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    broker.publish.return_value.is_published.return_value = True
    monkeypatch.setattr(mqtt, "Client", lambda **_: broker)
    cliente = conectar_mqtt(crear_entrada_persistente(notas), encolar_respuesta=partes.guardar_respuesta)
    cliente.on_connect(cliente, None, None, 0, None)
    nota = crear_nota()
    alerta = nota.model_copy(update={"id": f"NOTA-{condicion}", "audio_url": f"/media/audios/{condicion}.wav"})
    wav = crear_wav_prueba()
    audio_urls = []

    def descargar(request):
        audio_urls.append(request.url.path)
        return httpx.Response(200, content=wav)

    cliente_http = httpx.Client
    monkeypatch.setenv("PLANTA_API_URL", "http://sim:8100")
    monkeypatch.setattr(httpx, "Client", lambda **opciones: cliente_http(
        transport=httpx.MockTransport(descargar), **opciones,
    ))

    def api(url, **_):
        datos = catalogo() if url.endswith("/api/planta") else []
        assert url.startswith(("http://sim:8100/api/planta", "http://sim:8100/api/historian/"))
        return httpx.Response(200, json=datos, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", api)
    planta = ClientePlanta("http://sim:8100")
    worker = WorkerPartes(partes, planta.catalogo, historial=planta.historial)
    texto = "La dos está en 1640 de descarga."
    stt = MagicMock()
    stt.transcribir.side_effect = [texto, frase]
    procesar = crear_procesador_persistente(notas, stt)

    def recibir(topico, modelo):
        cliente.on_message(cliente, None, SimpleNamespace(
            topic=topico, payload=modelo.model_dump_json().encode("utf-8"),
        ))

    desde_nota = perf_counter()
    recibir(Topicos.RECORRIDAS, nota)
    recibir(Topicos.RECORRIDAS, nota)
    assert procesar(notas.reclamar_siguiente())
    assert notas.obtener_transcripcion(nota.id) == texto
    assert worker.procesar_siguiente()
    assert not publicar_siguiente(alertas, cliente)
    assert publicar_siguiente(borradores, cliente)
    hasta_borrador = perf_counter() - desde_nota
    envio = cliente.publish.call_args.kwargs
    assert envio["topic"] == Topicos.TABLET
    borrador = MensajeTablet.model_validate_json(envio["payload"], strict=True)
    assert borrador.espera_respuesta and borrador.nota_ids == [nota.id]
    assert [(fila.tag, fila.valor, fila.unidad) for fila in borrador.pantalla.filas] == [("B-102", 1640, "psi")]
    assert not borrador.pantalla.opciones

    desde_alerta = perf_counter()
    recibir(Topicos.RECORRIDAS, alerta)
    assert procesar(notas.reclamar_siguiente())
    assert not publicar_siguiente(borradores, cliente)
    assert publicar_siguiente(alertas, cliente)
    hasta_alerta = perf_counter() - desde_alerta
    evento = EventoSistema.model_validate_json(cliente.publish.call_args.kwargs["payload"], strict=True)
    assert evento.id == f"SEG-{alerta.id}-{condicion}" and evento.nota_ids == [alerta.id]
    assert publicar_siguiente(alertas, cliente)
    aviso = MensajeTablet.model_validate_json(cliente.publish.call_args.kwargs["payload"], strict=True)
    assert aviso.id == f"TABLET-SEG-{alerta.id}" and not aviso.espera_respuesta
    assert not publicar_siguiente(alertas, cliente)

    r = respuesta(partes.obtener(nota.id), "Correcto, dale.")
    recibir(Topicos.RESPUESTAS, r)
    recibir(Topicos.RESPUESTAS, r)
    _, _, _, reabierto, worker = preparar(tmp_path)
    desde_respuesta = perf_counter()
    assert worker.procesar_siguiente()
    tiempo_respuesta = perf_counter() - desde_respuesta
    assert resultado(reabierto, r.id) == "confirmada"
    assert reabierto.obtener(nota.id).estado == "confirmado"
    assert reabierto.obtener(nota.id).filas == borrador.pantalla.filas
    assert not publicar_siguiente(borradores, cliente)
    publicaciones = [c.kwargs for c in cliente.publish.call_args_list]
    ids = [json.loads(p["payload"])["id"] for p in publicaciones]
    assert len(ids) == len(set(ids)) == 3
    assert all(p["qos"] == 1 for p in publicaciones)
    assert audio_urls == [nota.audio_url, alerta.audio_url] and stt.transcribir.call_count == 2
    print(json.dumps({
        "prueba": "integracion_con_dobles", "condicion": condicion, "publicaciones_validas": len(ids),
        "duplicados": len(ids) - len(set(ids)), "reinicio": True,
        "nota_a_borrador_s": hasta_borrador, "nota_a_alerta_s": hasta_alerta,
        "respuesta_a_confirmado_s": tiempo_respuesta,
        "activo_sin_espera_operador_s": hasta_borrador + tiempo_respuesta,
    }))


def test_heartbeat_integrado_solicita_intervalo_y_publica_contrato():
    broker = MagicMock()
    broker.conectado = Event()
    broker.conectado.set()
    broker.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    detener = MagicMock()
    detener.is_set.side_effect = [False, False, False, False, True]
    ejecutar_heartbeat(broker, detener)

    assert [c.args for c in detener.wait.call_args_list] == [(30,), (30,)]
    for llamada in broker.publish.call_args_list:
        envio = llamada.kwargs
        assert envio["topic"] == Topicos.ESTADO and envio["qos"] == 1
        EstadoSistema.model_validate_json(envio["payload"], strict=True)
    print(json.dumps({"prueba": "heartbeat_con_dobles", "publicaciones_validas": 2, "intervalo_configurado_s": 30}))


@pytest.mark.parametrize("carga_b1", [False, True], ids=["sin-carga", "cola-saturada"])
@pytest.mark.parametrize(("texto", "codigo"), [
    ("¿Hay un choro de agua presión sale agua presión en la bomba?", None),
    ("Hay oloracas, se siente oloracas.", None),
    ("Ahí un buen juego, está saliendo un modo de la bomba.", None),
    ("Hay una persona herida, una persona está lesionada.", "S4"),
    ("Hay un chorro de agua a presión. Sale agua a presión en la bomba.", "S1"),
    ("Hay olor a gas. Se siente olor a gas.", "S2"),
    ("Hay humo y fuego. Está saliendo humo de la bomba.", "S3"),
    ("Hay una persona herida. Una persona está lesionada.", "S4"),
], ids=["stt-s1", "stt-s2", "stt-s3", "stt-s4", "referencia-s1", "referencia-s2", "referencia-s3", "referencia-s4"])
def test_seguridad_por_etapas_con_transcripciones_de_control(tmp_path, monkeypatch, carga_b1, texto, codigo):
    notas, salida, _, _, _ = preparar(tmp_path)
    _, _, sensores, _ = preparar_sensores(tmp_path, ruta=notas.ruta, capacidad=1)
    broker = MagicMock()
    broker.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    broker.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS
    broker.publish.return_value.is_published.return_value = True
    monkeypatch.setattr(mqtt, "Client", lambda **_: broker)
    cliente = conectar_mqtt(crear_entrada_persistente(notas), encolar_sensor=sensores.encolar)
    cliente.on_connect(cliente, None, None, 0, None)

    nota = crear_nota()
    wav = crear_wav_prueba()
    descargar = MagicMock(return_value=httpx.Response(200, content=wav))
    cliente_http = httpx.Client
    monkeypatch.setenv("PLANTA_API_URL", "http://sim:8100")
    monkeypatch.setattr(httpx, "Client", lambda **opciones: cliente_http(
        transport=httpx.MockTransport(descargar), **opciones,
    ))
    motor = MagicMock()
    motor.transcribir.return_value = texto
    procesar = crear_procesador_persistente(notas, motor)

    if carga_b1:
        for instante in (0, 60, 120):
            cliente.on_message(cliente, None, mensaje_sensor(instante, 130))
        assert sensores.perdidas == 2
    cliente.on_message(cliente, None, SimpleNamespace(
        topic=Topicos.RECORRIDAS, payload=nota.model_dump_json().encode(),
    ))
    assert notas.obtener_estado(nota.id) == "pendiente"
    assert procesar(notas.reclamar_siguiente())
    descargar.assert_called_once()
    assert descargar.call_args.args[0].url.path == nota.audio_url
    motor.transcribir.assert_called_once_with(wav)
    assert notas.obtener_estado(nota.id) == "completada"
    assert notas.obtener_transcripcion(nota.id) == texto

    assert salida.pendientes() == (2 if codigo else 0)
    if codigo:
        assert publicar_siguiente(salida, cliente)
        evento = EventoSistema.model_validate_json(broker.publish.call_args.kwargs["payload"], strict=True)
        assert evento.id == f"SEG-{nota.id}-{codigo}" and evento.nota_ids == [nota.id]
        assert publicar_siguiente(salida, cliente)
        aviso = MensajeTablet.model_validate_json(broker.publish.call_args.kwargs["payload"], strict=True)
        assert aviso.id == f"TABLET-SEG-{nota.id}" and not aviso.espera_respuesta
    assert not publicar_siguiente(salida, cliente)
    assert [c.kwargs["topic"] for c in broker.publish.call_args_list] == (
        [Topicos.EVENTOS, Topicos.TABLET] if codigo else []
    )
