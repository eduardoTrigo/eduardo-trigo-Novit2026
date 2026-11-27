import json
import os
import socket
import sqlite3
import subprocess
import sys
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest
from planta_contrato import MensajeTablet, NotaRecorrida, RespuestaOperador

from sistema.part_memory import RepositorioMemoriaPostgres, WorkerMemoria, consultar_memoria
from sistema.part_memory_store import AlmacenMemoriaLocal
from sistema.part_worker import WorkerPartes
from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres, conectar_postgres
from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import procesar, respuesta
from test_security_messages import crear_nota


pytestmark = pytest.mark.skipif(
    os.getenv("PIA3_POSTGRES_INTEGRATION") != "1",
    reason="Requiere PostgreSQL aislado con migración 002 y PIA3_POSTGRES_INTEGRATION=1",
)


def test_persiste_conversacion_real_del_worker_y_recupera_tras_fallos(tmp_path, monkeypatch):
    notas, _, _, partes, _ = preparar(tmp_path)
    nota = crear_nota().model_copy(update={"id": f"NOTA-MEM-{uuid4().hex}",
                                         "fotos": ["/media/fotos/memoria.jpg"]})
    texto = "La uno está en 110 kilos de descarga. Saqué foto del manómetro de descarga de la uno."
    transcribir_nota(notas, nota, texto)
    binario = b"\xff\xd8\xffprueba\xff\xd9"
    worker = WorkerPartes(partes, definicion_planta, foto=lambda ruta, *, plazo: ("image/jpeg", binario))
    assert worker.procesar_siguiente()
    local = AlmacenMemoriaLocal(partes.ruta)
    inicial = local.siguiente()
    config = ConfiguracionPostgres.desde_entorno()
    with socket.socket() as puerto_sin_servidor:
        puerto_sin_servidor.bind(("127.0.0.1", 0))
        caido = RepositorioMemoriaPostgres(replace(config, host="127.0.0.1", puerto=puerto_sin_servidor.getsockname()[1]))
        with pytest.raises(ErrorConexionPostgres):
            WorkerMemoria(local, caido).sincronizar_siguiente()
    assert local.siguiente() == inicial

    _, _, _, partes, worker = preparar(tmp_path)
    cambio = respuesta(partes.obtener(nota.id), "No, la uno estaba en ciento quince kilos.",
                       identificador=f"RESP-{uuid4().hex}")
    procesar(partes, worker, cambio)
    assert not partes.guardar_respuesta(cambio)
    _, _, _, partes, worker = preparar(tmp_path)
    confirmacion = respuesta(partes.obtener(nota.id), "Sí, confirmo.", identificador=f"RESP-{uuid4().hex}")
    procesar(partes, worker, confirmacion)
    local = AlmacenMemoriaLocal(partes.ruta)
    remoto = RepositorioMemoriaPostgres(config)
    sincronizador = WorkerMemoria(local, remoto)
    assert consultar_memoria(nota.id, local, remoto)["sincronizacion"] == "pendiente"
    final = local.siguiente()
    with patch.object(local, "confirmar", side_effect=sqlite3.OperationalError("reconocimiento interrumpido")):
        with pytest.raises(sqlite3.OperationalError):
            sincronizador.sincronizar_siguiente()
    assert remoto.obtener(nota.id) == final and local.siguiente() == final
    with conectar_postgres(config) as db:
        marca = db.execute("SELECT sincronizada_en FROM memoria.partes WHERE nota_id = %s", (nota.id,)).fetchone()

    local = AlmacenMemoriaLocal(partes.ruta)
    assert WorkerMemoria(local, remoto).sincronizar_siguiente()
    assert local.siguiente() is None
    remoto.guardar(inicial)
    with conectar_postgres(config) as db:
        assert db.execute("SELECT sincronizada_en FROM memoria.partes WHERE nota_id = %s", (nota.id,)).fetchone() == marca
    assert remoto.obtener(nota.id) == final
    assert remoto.obtener(f"NOTA-MEM-{uuid4().hex}") is None

    monkeypatch.setenv("PIA3_DB_PATH", str(partes.ruta))
    consulta = subprocess.run(
        [sys.executable, "-m", "sistema.part_memory", nota.id], capture_output=True, text=True, check=True, timeout=15,
    )
    recuperada = json.loads(consulta.stdout)
    assert recuperada["sincronizacion"] == "sincronizada"
    memoria = recuperada["memoria"]
    assert NotaRecorrida.model_validate(memoria["nota"]) == nota
    assert memoria["procesamiento_nota"]["intentos_stt"] == 1
    assert memoria["parte"]["estado"] == "confirmado" and memoria["parte"]["interacciones"] == 2
    mensajes = [MensajeTablet.model_validate(b["mensaje"]) for b in memoria["borradores"]]
    assert [m.id for m in mensajes] == [cambio.mensaje_id, confirmacion.mensaje_id]
    assert [m.pantalla.filas[0].valor for m in mensajes] == [110, 115]
    assert [RespuestaOperador.model_validate(r["payload"]) for r in memoria["respuestas"]] == [cambio, confirmacion]
    foto, = memoria["parte"]["contexto"]["fotos"]
    assert foto["tag"] == "B-101" and foto["variable"] == "presion_descarga_local"
    assert [b["contexto"]["fotos"] for b in memoria["borradores"]] == [[foto], [foto]]
    assert partes.obtener_foto(foto["sha256"]) == ("image/jpeg", binario)
    assert "contenido" not in foto


def test_proyecta_objetivo_pendiente_y_asociacion_resuelta_en_versiones_distintas(tmp_path):
    notas, _, _, partes, _ = preparar(tmp_path)
    nota = crear_nota().model_copy(update={"id": f"NOTA-MEM-{uuid4().hex}",
                                         "fotos": ["/media/fotos/memoria-aclarada.jpg"]})
    texto = "La dos 1640 pc de descarga. Saqué foto del manómetro de descarga de la dos."
    transcribir_nota(notas, nota, texto)
    binario = b"\xff\xd8\xffprueba\xff\xd9"
    worker = WorkerPartes(partes, definicion_planta, foto=lambda ruta, *, plazo: ("image/jpeg", binario))
    assert worker.procesar_siguiente()
    remoto = RepositorioMemoriaPostgres(ConfiguracionPostgres.desde_entorno())
    memoria = WorkerMemoria(AlmacenMemoriaLocal(partes.ruta), remoto)
    assert memoria.sincronizar_siguiente()
    pendiente = remoto.obtener(nota.id).documento["parte"]["contexto"]["fotos"][0]
    assert pendiente["estado_asociacion"] == "nota" and pendiente["motivo"] == "fila_pendiente"

    _, _, _, partes, worker = preparar(tmp_path)
    procesar(partes, worker, respuesta(partes.obtener(nota.id), "psi", identificador=f"RESP-{uuid4().hex}"))
    procesar(partes, worker, respuesta(partes.obtener(nota.id), "Confirmo", identificador=f"RESP-{uuid4().hex}"))
    assert memoria.sincronizar_siguiente()
    documento = remoto.obtener(nota.id).documento
    assert documento["parte"]["estado"] == "confirmado"
    inicial, final = [b["contexto"]["fotos"][0] for b in documento["borradores"]]
    assert inicial == pendiente
    assert (final["estado_asociacion"], final["tag"], final["variable"]) == (
        "fila", "B-102", "presion_descarga_local",
    )
    assert final["id"] == inicial["id"] and final["sha256"] == inicial["sha256"]
    assert documento["parte"]["contexto"]["fotos"] == [final]


def test_rol_de_aplicacion_solo_puede_leer_y_proyectar():
    config = ConfiguracionPostgres.desde_entorno()
    with conectar_postgres(config) as db:
        assert db.execute("""
            SELECT has_schema_privilege('memoria', 'USAGE'), has_schema_privilege('memoria', 'CREATE'),
                   has_table_privilege('memoria.partes', 'SELECT'), has_table_privilege('memoria.partes', 'INSERT'),
                   has_column_privilege('memoria.partes', 'documento', 'UPDATE'),
                   has_column_privilege('memoria.partes', 'nota_id', 'UPDATE'),
                   has_table_privilege('memoria.partes', 'DELETE'), has_table_privilege('memoria.partes', 'TRUNCATE')
        """).fetchone() == (True, False, True, True, True, False, False, False)


def test_timeout_sql_es_efectivo():
    config = ConfiguracionPostgres.desde_entorno()
    with pytest.raises(psycopg.errors.QueryCanceled):
        with conectar_postgres(config, timeout_sentencia_ms=100) as db:
            db.execute("SELECT pg_sleep(2)")
