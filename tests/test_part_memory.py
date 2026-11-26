import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest
from planta_contrato import MensajeTablet, NotaRecorrida

from sistema.part_memory import WorkerMemoria, consultar_memoria
from sistema.part_memory_store import AlmacenMemoriaLocal
from sistema.postgres import ErrorConexionPostgres
from test_part_audio_reply import worker_audio
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import preparar_borrador, procesar, respuesta
from test_security_messages import crear_nota


def test_historial_de_tablet_respuestas_y_cierre_sin_version_nueva(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    local = AlmacenMemoriaLocal(partes.ruta)
    inicial = partes.obtener(nota.id)
    esperado = MensajeTablet.model_validate_json(borradores.reclamar()["payload"])
    cambio = respuesta(inicial, "No, la uno estaba en ciento quince kilos.")
    procesar(partes, worker, cambio)
    assert not partes.guardar_respuesta(cambio)
    vigente = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(inicial, "Confirmo", identificador="RESP-TARDE"))
    antes = local.siguiente()
    procesar(partes, worker, respuesta(vigente, "Confirmo", identificador="RESP-CIERRE"))
    despues = local.siguiente()

    assert despues.revision > antes.revision
    memoria = despues.documento
    assert NotaRecorrida.model_validate(memoria["nota"]) == nota
    assert memoria["parte"]["version"] == antes.documento["parte"]["version"] == 2
    assert memoria["parte"]["estado"] == "confirmado"
    assert memoria["parte"]["interacciones"] == 2
    assert memoria["resuelta_en"]
    assert [b["filas"][0]["valor"] for b in memoria["borradores"]] == [110, 115]
    assert MensajeTablet.model_validate(memoria["borradores"][0]["mensaje"]) == esperado
    assert [r["resultado"] for r in memoria["respuestas"]] == ["corregida", "desfasada", "confirmada"]


def test_stt_y_duplicado_no_aumentan_interacciones(tmp_path, monkeypatch):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    audio = respuesta(inicial, "", audio="/media/audios/RESP-001.wav")
    procesar(partes, worker, audio)
    procesar(partes, worker, audio.model_copy(update={"id": "RESP-DUP"}))
    local = AlmacenMemoriaLocal(partes.ruta)
    antes = local.siguiente()
    worker, _ = worker_audio(partes, monkeypatch, Mock(return_value="Sí, confirmo."))
    assert worker.procesar_siguiente()
    durante = local.siguiente()
    assert durante.revision > antes.revision
    assert durante.documento["parte"]["interacciones"] == 0
    assert durante.documento["procesamiento_nota"]["intentos_stt"] == 1
    primera, repetida = durante.documento["respuestas"]
    assert primera["intentos_stt"] == 1 and primera["resultado"] == "stt_listo"
    assert repetida["intentos_stt"] == 0 and repetida["resultado"] == "duplicada"


def test_rollback_y_reinicio_conservan_solo_cambios_confirmados(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    local = AlmacenMemoriaLocal(partes.ruta)
    cambio = respuesta(partes.obtener(nota.id), "No, la uno estaba en ciento quince kilos.")
    assert partes.guardar_respuesta(cambio)
    antes = local.siguiente()
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("""CREATE TRIGGER fallar BEFORE UPDATE OF resultado ON respuestas_parte
                      WHEN NEW.resultado = 'corregida'
                      BEGIN SELECT RAISE(ABORT, 'fallo después de actualizar el parte'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert local.siguiente() == antes
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("DROP TRIGGER fallar")
    _, _, _, reabierto, worker_reabierto = preparar(tmp_path)
    assert AlmacenMemoriaLocal(reabierto.ruta).siguiente() == antes
    assert worker_reabierto.procesar_siguiente()
    assert reabierto.obtener(nota.id).version == 2


def test_pendiente_duradero_sin_postgres_y_ack_solo_despues_de_guardar(tmp_path):
    nota, _, _, partes, _ = preparar_borrador(tmp_path)
    local = AlmacenMemoriaLocal(partes.ruta)
    remota = Mock()
    remota.guardar.side_effect = ErrorConexionPostgres("no disponible")
    worker = WorkerMemoria(local, remota)
    antes = local.siguiente()
    with pytest.raises(ErrorConexionPostgres):
        worker.sincronizar_siguiente()
    assert local.siguiente() == antes
    assert consultar_memoria(nota.id, local, Mock(obtener=lambda _: None))["sincronizacion"] == "pendiente"

    remota.guardar.side_effect = None
    local_reabierto = AlmacenMemoriaLocal(partes.ruta)
    assert WorkerMemoria(local_reabierto, remota).sincronizar_siguiente()
    remota.guardar.assert_called_with(antes)
    assert local.siguiente() is None


def test_nota_sin_parte_tambien_es_recuperable_y_aislada(tmp_path):
    notas, _, _, partes, worker = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, "La uno está en 110 kilos de descarga")
    assert worker.procesar_siguiente()
    otra = nota.model_copy(update={"id": "NOTA-OTRA"})
    assert notas.guardar(otra)
    local = AlmacenMemoriaLocal(partes.ruta)
    primero = local.siguiente()
    assert primero.nota_id == nota.id
    local.confirmar(primero)
    segundo = local.siguiente()
    assert segundo.nota_id == otra.id and segundo.documento["parte"] is None
    assert segundo.documento["borradores"] == segundo.documento["respuestas"] == []


def test_backfill_historico_no_inventa_payload_ni_fecha_de_cierre(tmp_path, monkeypatch):
    with monkeypatch.context() as anterior:
        anterior.setattr("sistema.part_store.crear_cola_memoria", lambda _: None)
        nota, _, _, partes, worker = preparar_borrador(tmp_path)
        parte = partes.obtener(nota.id)
        procesar(partes, worker, respuesta(parte, "Confirmo"))
        with closing(sqlite3.connect(partes.ruta)) as db, db:
            db.execute("INSERT INTO borradores_parte (mensaje_id, nota_id, version, filas) VALUES (?, ?, ?, ?)",
                       ("PARTE-HISTORICA", nota.id, 0, "[]"))

    _, _, _, partes, _ = preparar(tmp_path)
    captura = AlmacenMemoriaLocal(partes.ruta).siguiente()
    assert captura.revision == 1
    assert captura.documento["resuelta_en"] is None and captura.documento["parte"]["estado"] == "confirmado"
    historico, conocido = captura.documento["borradores"]
    assert historico["mensaje_id"] == "PARTE-HISTORICA" and historico["mensaje"] is None
    assert conocido["mensaje"]["id"] == parte.mensaje_id


def test_ack_anterior_mantiene_revision_concurrente_pendiente(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    local = AlmacenMemoriaLocal(partes.ruta)
    antes = local.siguiente()
    mensaje = respuesta(partes.obtener(nota.id), "Confirmo")
    remoto = Mock()
    # Emula una modificación local mientras PostgreSQL está recibiendo la captura anterior.
    remoto.guardar.side_effect = lambda _: procesar(partes, worker, mensaje)
    assert WorkerMemoria(local, remoto).sincronizar_siguiente()
    assert local.siguiente().revision > antes.revision
    assert local.siguiente().documento["parte"]["estado"] == "confirmado"
