"""Proyección PostgreSQL de memoria conversacional y sincronización recuperable."""

import argparse
import json
import logging
import os
import sqlite3
import sys
import threading

import psycopg
from psycopg.types.json import Jsonb

from sistema.part_memory_store import AlmacenMemoriaLocal, CapturaMemoria
from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres, conectar_postgres


logger = logging.getLogger(__name__)


class RepositorioMemoriaPostgres:
    def __init__(self, config: ConfiguracionPostgres):
        self.config = config

    def guardar(self, captura: CapturaMemoria) -> None:
        with conectar_postgres(self.config, timeout_sentencia_ms=3000) as db:
            db.execute("""
                INSERT INTO memoria.partes AS actual (nota_id, revision, documento) VALUES (%s, %s, %s)
                ON CONFLICT (nota_id) DO UPDATE SET revision = excluded.revision,
                    documento = excluded.documento, sincronizada_en = statement_timestamp()
                WHERE actual.revision < excluded.revision
            """, (captura.nota_id, captura.revision, Jsonb(captura.documento)))

    def obtener(self, nota_id: str) -> CapturaMemoria | None:
        with conectar_postgres(self.config, timeout_sentencia_ms=3000) as db:
            fila = db.execute(
                "SELECT revision, documento FROM memoria.partes WHERE nota_id = %s", (nota_id,)
            ).fetchone()
        return CapturaMemoria(nota_id, *fila) if fila else None


class WorkerMemoria:
    def __init__(self, local: AlmacenMemoriaLocal, remoto: RepositorioMemoriaPostgres):
        self.local = local
        self.remoto = remoto
        self.detencion = threading.Event()
        self.hilo = threading.Thread(target=self._ejecutar, name="pia3-memoria", daemon=True)

    def sincronizar_siguiente(self) -> bool:
        captura = self.local.siguiente()
        if captura is None:
            return False
        self.remoto.guardar(captura)
        self.local.confirmar(captura)
        return True

    def iniciar(self) -> None:
        self.hilo.start()

    def _ejecutar(self) -> None:
        espera = 1.0
        while not self.detencion.is_set():
            try:
                trabajo = self.sincronizar_siguiente()
            except (ErrorConexionPostgres, psycopg.Error, sqlite3.Error) as error:
                logger.warning("Memoria pendiente de sincronización (%s). Reintento en %.0f segundos",
                               type(error).__name__, espera)
                self.detencion.wait(espera)
                espera = min(espera * 2, 30.0)
            else:
                espera = 1.0
                self.detencion.wait(0.1 if trabajo else 1.0)

    def detener(self) -> bool:
        self.detencion.set()
        if self.hilo.ident is not None:
            self.hilo.join(timeout=5)
        return not self.hilo.is_alive()


def consultar_memoria(nota_id: str, local: AlmacenMemoriaLocal, remoto: RepositorioMemoriaPostgres) -> dict:
    captura = remoto.obtener(nota_id)
    revision_local = local.revision(nota_id)
    revision_remota = captura.revision if captura else None
    if revision_local is None:
        sincronizacion = "sin_referencia_local" if captura else "no_encontrada"
    elif revision_remota is None or revision_remota < revision_local:
        sincronizacion = "pendiente"
    elif revision_remota == revision_local:
        sincronizacion = "sincronizada"
    else:
        sincronizacion = "desalineada"
    return {
        "nota_id": nota_id, "revision_sqlite": revision_local, "revision_postgres": revision_remota,
        "sincronizacion": sincronizacion, "memoria": captura.documento if captura else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recupera memoria conversacional desde PostgreSQL")
    parser.add_argument("nota_id")
    args = parser.parse_args()
    ruta = os.getenv("PIA3_DB_PATH", "data_runtime/seguridad.sqlite3")
    try:
        remoto = RepositorioMemoriaPostgres(ConfiguracionPostgres.desde_entorno())
        memoria = consultar_memoria(args.nota_id, AlmacenMemoriaLocal(ruta), remoto)
    except (ValueError, ErrorConexionPostgres, psycopg.Error, sqlite3.Error):
        print("No se pudo consultar la memoria conversacional.", file=sys.stderr)
        return 1
    print(json.dumps(memoria))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
