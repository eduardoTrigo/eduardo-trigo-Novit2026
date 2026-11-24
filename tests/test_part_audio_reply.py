import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from unittest.mock import Mock

import pytest

from test_part_extraction import definicion_planta
from test_part_messages import preparar
from test_part_workflow import preparar_borrador, procesar, respuesta, resultado
from test_transcription_service import crear_wav_prueba


def worker_audio(partes, monkeypatch, transcribir):
    from sistema.part_worker import WorkerPartes

    descarga = Mock(return_value=crear_wav_prueba())
    monkeypatch.setattr("sistema.part_worker.descargar_audio", descarga, raising=False)
    return WorkerPartes(partes, definicion_planta, transcribir_respuesta=transcribir), descarga


def test_audio_de_tablet_se_reanuda_y_persiste_antes_de_aplicar(tmp_path, monkeypatch):
    nota, seguridad, borradores, partes, worker = preparar_borrador(tmp_path)
    base = partes.obtener(nota.id)
    r = respuesta(base, "", audio="/media/audios/RESP-001.wav")
    procesar(partes, worker, r)
    assert resultado(partes, r.id) == "requiere_stt"
    duplicada = r.model_copy(update={"id": "RESP-DUP"})
    procesar(partes, worker, duplicada)
    assert resultado(partes, duplicada.id) == "duplicada"

    _, _, _, partes, _ = preparar(tmp_path)
    stt = Mock(return_value="Sí, confirmo.")
    worker, descarga = worker_audio(partes, monkeypatch, stt)
    assert worker.procesar_siguiente()
    assert descarga.call_args.args == (r.audio_url,)
    stt.assert_called_once()
    assert resultado(partes, r.id) == "stt_listo"

    _, _, _, partes, _ = preparar(tmp_path)
    worker, _ = worker_audio(partes, monkeypatch, stt)
    assert worker.procesar_siguiente()
    final = partes.obtener(nota.id)
    assert final.estado == "confirmado" and final.interacciones == 1 and final.filas == base.filas
    assert resultado(partes, r.id) == "confirmada"
    assert not partes.guardar_respuesta(r)
    assert not worker.procesar_siguiente() and seguridad.pendientes() == 0 and borradores.pendientes() == 1
    stt.assert_called_once()


def test_audio_aplazado_no_cuenta_y_fallos_agotados_quedan_en_revision(tmp_path, monkeypatch):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    r = respuesta(partes.obtener(nota.id), "", audio="/media/audios/RESP-001.wav")
    procesar(partes, worker, r)
    reloj = [100.0]
    monkeypatch.setattr(time, "time", lambda: reloj[0])
    stt = Mock(side_effect=[None, TimeoutError("STT lento"), TimeoutError("STT lento"), TimeoutError("STT lento")])
    worker, _ = worker_audio(partes, monkeypatch, stt)
    assert worker.procesar_siguiente()
    assert partes.obtener(nota.id).interacciones == 0
    assert not worker.procesar_siguiente()
    for _ in range(3):
        reloj[0] += 10
        assert worker.procesar_siguiente()
    assert worker.procesar_siguiente()
    final = partes.obtener(nota.id)
    assert final.estado == "revision_manual" and final.interacciones == 1
    assert resultado(partes, r.id) == "stt_fallido" and stt.call_count == 4
    assert borradores.pendientes() == 1
    with closing(sqlite3.connect(partes.ruta)) as db:
        assert db.execute("SELECT error_stt FROM respuestas_parte WHERE id=?", (r.id,)).fetchone()[0]


def test_recupera_reserva_interrumpida_y_no_repite_stt_si_falla_aplicar(tmp_path, monkeypatch):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, "", audio="/media/audios/RESP-001.wav")
    procesar(partes, worker, r)
    reloj = [100.0]
    monkeypatch.setattr(time, "time", lambda: reloj[0])
    assert partes.reclamar_stt().intento == 1
    assert resultado(partes, r.id) == "stt_procesando"

    _, _, _, partes, _ = preparar(tmp_path)
    stt = Mock(return_value="La uno estaba en ciento quince kilos.")
    worker, descarga = worker_audio(partes, monkeypatch, stt)
    assert not worker.procesar_siguiente()
    descarga.assert_not_called()
    reloj[0] += partes.RESERVA_STT_S
    assert worker.procesar_siguiente() and resultado(partes, r.id) == "stt_listo"
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("""
            CREATE TRIGGER fallar_tras_stt BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT, 'fallo de prueba'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert partes.obtener(nota.id) == inicial and resultado(partes, r.id) == "stt_listo"
    with closing(sqlite3.connect(partes.ruta)) as db, db:
        db.execute("DROP TRIGGER fallar_tras_stt")
    assert worker.procesar_siguiente()
    final = partes.obtener(nota.id)
    assert final.version == 2 and final.interacciones == 1 and final.filas[0].valor == 115
    assert resultado(partes, r.id) == "corregida" and borradores.pendientes() == 2
    stt.assert_called_once()


def test_prioridad_del_motor_reusa_whisper_y_cede_a_nota_de_seguridad():
    from sistema.transcription_service import TranscripcionPrioritaria

    motor = Mock()
    motor.transcribir.return_value = "Hay humo."
    motor.transcribir_respuesta.return_value = "Confirmo."
    hay_seguridad = Mock(return_value=True)
    servicio = TranscripcionPrioritaria(motor, hay_seguridad)
    wav = crear_wav_prueba()
    assert servicio.transcribir_respuesta(wav) is None
    motor.transcribir_respuesta.assert_not_called()
    assert servicio.transcribir(wav) == "Hay humo."
    hay_seguridad.return_value = False
    assert servicio.transcribir_respuesta(wav) == "Confirmo."
    motor.transcribir.assert_called_once_with(wav)
    motor.transcribir_respuesta.assert_called_once_with(wav)


def test_tablet_no_espera_un_motor_ocupado_por_seguridad():
    from sistema.transcription_service import TranscripcionPrioritaria

    motor = Mock()
    ocupado = Event()
    liberar = Event()

    def transcribir_nota(_audio):
        ocupado.set()
        assert liberar.wait(5)
        return "Hay humo."

    motor.transcribir.side_effect = transcribir_nota
    servicio = TranscripcionPrioritaria(motor, lambda: False)
    wav = crear_wav_prueba()
    with ThreadPoolExecutor(max_workers=2) as pool:
        nota = pool.submit(servicio.transcribir, wav)
        try:
            assert ocupado.wait(3)
            assert pool.submit(servicio.transcribir_respuesta, wav).result(timeout=1) is None
        finally:
            liberar.set()
        assert nota.result(timeout=3) == "Hay humo."
    motor.transcribir_respuesta.assert_not_called()


def test_worker_pospone_descarga_con_una_nota_lista_o_en_proceso(tmp_path, monkeypatch):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    r = respuesta(partes.obtener(nota.id), "", audio="/media/audios/RESP-001.wav")
    procesar(partes, worker, r)
    notas, _, _, partes, _ = preparar(tmp_path)
    stt = Mock(return_value="Sí, confirmo.")
    worker, descarga = worker_audio(partes, monkeypatch, stt)
    worker.hay_seguridad = notas.hay_seguridad_lista
    seguridad = nota.model_copy(update={"id": "NOTA-SEG"})
    assert notas.guardar(seguridad)
    assert not worker.procesar_siguiente()
    assert notas.reclamar_siguiente() == seguridad
    assert not worker.procesar_siguiente()
    assert resultado(partes, r.id) == "requiere_stt"
    descarga.assert_not_called()
    stt.assert_not_called()
    assert notas.completar(seguridad.id)
    assert worker.procesar_siguiente() and resultado(partes, r.id) == "stt_listo"
