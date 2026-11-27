import sqlite3
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest
from planta_contrato import FilaParte, MensajeTablet, Topicos

from sistema.note_store import AlmacenNotas
from sistema.nota_router import recibir_nota_mqtt
from sistema.outbox_publisher import publicar_siguiente
from sistema.persistent_ingress import crear_entrada_persistente
from sistema.security_outbox import OutboxSeguridad
from sistema.security_persistent_processor import crear_procesador_persistente
from test_part_extraction import definicion_planta
from test_security_messages import crear_nota
from test_transcription_service import crear_wav_prueba


def preparar(tmp_path):
    from sistema.part_store import AlmacenPartes
    from sistema.part_worker import WorkerPartes

    notas = AlmacenNotas(tmp_path / "seguridad.sqlite3")
    seguridad = OutboxSeguridad(notas.ruta)
    borradores = OutboxSeguridad(notas.ruta, borradores=True)
    partes = AlmacenPartes(notas, borradores)
    worker = WorkerPartes(partes, definicion_planta)
    return notas, seguridad, borradores, partes, worker


def transcribir_nota(notas, nota, texto):
    assert notas.guardar(nota)
    assert notas.reclamar_siguiente() == nota
    motor = MagicMock()
    motor.transcribir.return_value = texto
    audio = crear_wav_prueba()
    with patch("sistema.security_pipeline.descargar_audio", return_value=audio) as descarga:
        assert crear_procesador_persistente(notas, motor)(nota)
    descarga.assert_called_once_with(nota.audio_url)
    motor.transcribir.assert_called_once_with(audio)
    return motor


def test_borrador_oficial_con_id_estable():
    from sistema.part_messages import crear_borrador

    nota = crear_nota()
    fila = FilaParte(tag="B-102", variable="presion_descarga_local", valor=1640, unidad="psi")
    mensaje = crear_borrador(nota, [fila])
    assert mensaje == MensajeTablet.model_validate_json(mensaje.model_dump_json())
    assert mensaje.id == crear_borrador(nota, [fila]).id == f"PARTE-{nota.id}-1"
    assert mensaje.nota_ids == [nota.id]
    assert mensaje.ts == nota.ts
    assert mensaje.espera_respuesta
    assert mensaje.pantalla.filas == [fila]
    assert mensaje.pantalla.opciones == []
    with pytest.raises(ValueError):
        FilaParte.model_validate({**fila.model_dump(), "foto": "inventada"})


def test_borrador_recuperable_y_publicado_una_vez_logicamente(tmp_path):
    notas, seguridad, borradores, partes, worker = preparar(tmp_path)
    nota = crear_nota().model_copy(update={"fotos": ["/media/fotos/ejemplo.jpg"]})
    motor = transcribir_nota(notas, nota, "La dos está en 1640 de descarga")
    assert worker.procesar_siguiente()
    parte = partes.obtener(nota.id)
    assert parte.nota == nota
    assert parte.estado == "borrador_pendiente"
    assert parte.filas[0].valor == 1640
    assert seguridad.pendientes() == 0
    assert borradores.reclamar()["id"] == parte.mensaje_id

    _, _, recuperados, partes_reabiertas, worker_reabierto = preparar(tmp_path)
    assert not worker_reabierto.procesar_siguiente()
    assert partes_reabiertas.obtener(nota.id) == parte
    assert recuperados.recuperar() == 1
    cliente = MagicMock()
    cliente.conectado.is_set.return_value = True
    cliente.publish.return_value.rc = 0
    cliente.publish.return_value.is_published.return_value = True
    assert publicar_siguiente(recuperados, cliente)
    assert not publicar_siguiente(recuperados, cliente)
    cliente.publish.assert_called_once()
    envio = cliente.publish.call_args.kwargs
    assert envio["topic"] == Topicos.TABLET and envio["qos"] == 1
    assert MensajeTablet.model_validate_json(envio["payload"]).id == parte.mensaje_id
    assert motor.transcribir.call_count == 1


def test_fallo_al_encolar_borrador_revierte_el_parte(tmp_path):
    notas, _, borradores, partes, worker = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, "La dos está en 1640 de descarga")
    with sqlite3.connect(notas.ruta) as db:
        db.execute("""
            CREATE TRIGGER bloquear_borrador BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT, 'fallo de prueba'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert partes.obtener(nota.id) is None
    assert borradores.pendientes() == 0
    assert notas.obtener_transcripcion(nota.id)


def test_vivo_sin_audio_conserva_evidencia_sin_publicar(tmp_path):
    notas, seguridad, borradores, partes, worker = preparar(tmp_path)
    nota = crear_nota().model_copy(update={
        "id": "VIVO-123", "audio_url": None, "fotos": ["/media/fotos/vivo/123.jpg"],
    })
    assert recibir_nota_mqtt(Topicos.RECORRIDAS, nota.model_dump_json().encode(),
                             crear_entrada_persistente(notas))
    assert notas.reclamar_siguiente() is None
    assert worker.procesar_siguiente()
    parte = partes.obtener(nota.id)
    assert parte.estado == "revision_manual" and parte.nota.fotos == nota.fotos
    assert parte.filas == [] and parte.mensaje_id is None
    assert seguridad.pendientes() == borradores.pendientes() == 0


@pytest.mark.parametrize(
    "texto,estado",
    [("Recorrida sin novedad", "sin_contenido"),
     ("La uno no está en 115 kilos de descarga", "revision_manual"),
     ("La tres gotea por la empaquetadura", "revision_manual")],
)
def test_notas_sin_borrador_evaluable(tmp_path, texto, estado):
    notas, _, borradores, partes, worker = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, texto)
    assert worker.procesar_siguiente()
    parte = partes.obtener(nota.id)
    assert parte.estado == estado and parte.mensaje_id is None
    assert all(f.valor is None for f in parte.filas)
    assert notas.obtener_transcripcion(nota.id) == texto
    assert borradores.pendientes() == 0


def test_revision_manual_conserva_lectura_parcial_y_transcripcion_tras_reinicio(tmp_path):
    notas, _, _, partes, worker = preparar(tmp_path)
    nota = crear_nota()
    texto = "La dos está en 1640 de descarga. La uno no está en 115 kilos de descarga."
    motor = transcribir_nota(notas, nota, texto)
    assert worker.procesar_siguiente()
    parte = partes.obtener(nota.id)
    assert parte.estado == "revision_manual" and parte.mensaje_id is None
    assert [(f.tag, f.valor, f.unidad) for f in parte.filas] == [("B-102", 1640, "psi")]
    assert parte.contexto["no_interpretada"] and parte.contexto["pendientes"] == []

    notas, seguridad, borradores, reabiertas, worker = preparar(tmp_path)
    assert reabiertas.obtener(nota.id) == parte
    assert notas.obtener_transcripcion(nota.id) == texto
    assert not worker.procesar_siguiente()
    assert seguridad.pendientes() == borradores.pendientes() == 0
    motor.transcribir.assert_called_once()


@pytest.mark.parametrize("con_turno_anterior", [False, True])
def test_recupera_partes_anteriores_sin_aclaracion_vigente(tmp_path, con_turno_anterior):
    from sistema.part_messages import crear_aclaracion

    notas, _, _, partes, _ = preparar(tmp_path)
    nota = crear_nota()
    texto = "La dos está en 1640 de descarga. La uno no está en 115 kilos de descarga."
    transcribir_nota(notas, nota, texto)
    fila = FilaParte(tag="B-102", variable="presion_descarga_local", valor=1640, unidad="psi")
    contexto = {"no_interpretada": True, "pendientes": [], "resoluciones": [{"respuesta_id": "RESP-HIST"}]}
    anterior = crear_aclaracion(
        nota, "¿Qué bomba mediste?", version=1, opciones=["B-102"], filas=[],
    ) if con_turno_anterior else None
    assert partes.guardar(nota, "revision_manual", [fila], anterior, contexto)
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("UPDATE partes SET estado = 'aclaracion_pendiente' WHERE nota_id = ?", (nota.id,))
        outbox_previa = db.execute("SELECT * FROM outbox").fetchall()

    notas, _, _, reabiertas, worker = preparar(tmp_path)
    parte = reabiertas.obtener(nota.id)
    assert parte.estado == "revision_manual"
    assert parte.mensaje_id == (anterior.id if anterior else None)
    assert parte.filas == [fila] and parte.contexto == contexto
    assert notas.obtener_transcripcion(nota.id) == texto
    assert not worker.procesar_siguiente()
    with closing(sqlite3.connect(reabiertas.ruta)) as db:
        assert db.execute("SELECT * FROM outbox").fetchall() == outbox_previa
    assert preparar(tmp_path)[3].obtener(nota.id) == parte


def test_carril_de_seguridad_independiente_de_un_borrador_en_envio(tmp_path):
    notas, seguridad, borradores, _, worker = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, "La dos está en 1640 de descarga")
    assert worker.procesar_siguiente()
    assert borradores.reclamar()["id"] == f"PARTE-{nota.id}-1"
    insegura = nota.model_copy(update={"id": "NOTA-SEG"})
    transcribir_nota(notas, insegura, "Sale humo de la bomba")
    assert seguridad.pendientes() == 2
    assert seguridad.recuperar() == 0
    assert seguridad.reclamar()["id"] == f"SEG-{insegura.id}-S3"
    assert seguridad.reclamar()["id"] == f"TABLET-SEG-{insegura.id}"
    assert borradores.recuperar() == 1


def test_entrypoint_conecta_worker_y_publicadores_sin_repetir_stt(tmp_path, monkeypatch):
    from sistema import security_integrated
    from sistema.part_worker import WorkerPartes

    ruta = tmp_path / "seguridad.sqlite3"
    notas = AlmacenNotas(ruta)
    OutboxSeguridad(ruta)
    nota = crear_nota()
    transcribir_nota(notas, nota, "La dos está en 1640 de descarga")
    monkeypatch.setenv("PIA3_DB_PATH", str(ruta))
    monkeypatch.setenv("PIA3_ENABLE_OUTBOX", "1")
    monkeypatch.setattr(security_integrated, "conectar_mqtt", MagicMock())
    monkeypatch.setattr(security_integrated, "ejecutar_heartbeat", MagicMock())
    whisper = MagicMock()
    monkeypatch.setattr(security_integrated, "WhisperLocal", lambda **opciones: whisper)
    monkeypatch.setattr(security_integrated.WorkerPersistente, "iniciar", lambda self: None)
    monkeypatch.setattr(security_integrated.ClientePlanta, "catalogo", lambda self, **kw: definicion_planta())
    monkeypatch.setattr(security_integrated.ClientePlanta, "historial", lambda *args, **kw: [], raising=False)
    publicadores = MagicMock()
    monkeypatch.setattr(security_integrated, "WorkerOutbox", publicadores)

    def un_ciclo_y_salir(worker):
        assert worker.procesar_siguiente()
        raise KeyboardInterrupt

    monkeypatch.setattr(WorkerPartes, "iniciar", un_ciclo_y_salir)
    security_integrated.main()

    carriles = [llamada.kwargs["outbox"].borradores for llamada in publicadores.call_args_list]
    assert carriles == [False, True]
    assert OutboxSeguridad(ruta, borradores=True).reclamar()["id"] == f"PARTE-{nota.id}-1"
    whisper.transcribir.assert_not_called()
