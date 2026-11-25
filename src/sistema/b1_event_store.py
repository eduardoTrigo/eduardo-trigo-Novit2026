"""Traspaso duradero de decisiones B1 y acceso a su evidencia."""

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from datetime import datetime, timedelta

from planta_contrato import EventoSistema, NotaRecorrida, TipoEvento, Topicos

from sistema.b1_events import evento_de_parte, evento_de_sensor, referencias_fotos
from sistema.security_outbox import OutboxSeguridad


class AlmacenEventosB1:
    MAX_INTENTOS_CONTEXTO = 3

    def __init__(self, outbox: OutboxSeguridad, *, reloj: Callable[[], float] = time.time):
        if not outbox.b1:
            raise ValueError("Los eventos B1 requieren el carril B1")
        self.ruta = outbox.ruta
        self.outbox = outbox
        self.reloj = reloj
        self._preferir_sensor = False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS eventos_b1 (
                    id TEXT PRIMARY KEY NOT NULL REFERENCES outbox(id),
                    origen TEXT NOT NULL,
                    equipo TEXT NOT NULL,
                    nota_id TEXT,
                    version INTEGER,
                    detalle TEXT NOT NULL,
                    contexto TEXT NOT NULL,
                    intentos_contexto INTEGER NOT NULL DEFAULT 0,
                    proximo_contexto REAL NOT NULL DEFAULT 0,
                    UNIQUE (nota_id, version, equipo)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS eventos_b1_contexto_disponible
                ON eventos_b1 (proximo_contexto) WHERE contexto = 'pendiente'
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS fotos_evento_b1 (
                    evento_id TEXT NOT NULL REFERENCES eventos_b1(id),
                    ruta TEXT NOT NULL,
                    mime TEXT NOT NULL,
                    contenido BLOB NOT NULL,
                    PRIMARY KEY (evento_id, ruta)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS episodios_b1_pendientes
                ON episodios_sensor_b1 (abierto) WHERE publicacion = 'pendiente'
            """)

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.ruta)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    @staticmethod
    def _parte(db):
        return db.execute("""
            SELECT d.nota_id, d.version, d.equipo, d.resultado, d.evidencia,
                   t.parte_id, t.filas, t.huella_catalogo, n.payload AS nota
            FROM decisiones_b1 d
            JOIN trabajos_b1 t USING (nota_id, version)
            JOIN notas n ON n.id = t.nota_id
            JOIN partes p ON p.nota_id = t.nota_id AND p.version = t.version
            WHERE d.resultado IN ('SinNovedad', 'Escalado')
              AND t.estado = 'clasificado' AND p.estado = 'confirmado'
              AND NOT EXISTS (
                  SELECT 1 FROM eventos_b1 e
                  WHERE e.nota_id = d.nota_id AND e.version = d.version AND e.equipo = d.equipo
              )
            ORDER BY d.rowid LIMIT 1
        """).fetchone()

    @staticmethod
    def _sensor(db):
        return db.execute("""
            SELECT id, epoca, equipo, variable, direccion, inicio, abierto, huella_limites, evidencia
            FROM episodios_sensor_b1 WHERE publicacion = 'pendiente' AND decision = 'Escalado'
            ORDER BY abierto, rowid LIMIT 1
        """).fetchone()

    def encolar_siguiente(self) -> bool:
        fuentes = ((self._sensor, evento_de_sensor), (self._parte, evento_de_parte))
        if not self._preferir_sensor:
            fuentes = fuentes[::-1]
        with self._db() as db:
            for leer, construir in fuentes:
                fila = leer(db)
                if fila is not None:
                    break
            else:
                return False

        self._preferir_sensor = construir is evento_de_parte
        evento, detalle = construir(fila)
        payload = evento.model_dump_json()
        EventoSistema.model_validate_json(payload, strict=True)
        evidencia = json.dumps(detalle, ensure_ascii=False, allow_nan=False)
        contexto = "pendiente" if evento.tipo == TipoEvento.escalado else "no_aplica"
        nota_id, version = (fila["nota_id"], fila["version"]) if evento.origen == "parte" else (None, None)

        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM eventos_b1 WHERE id = ?", (evento.id,)).fetchone():
                return False
            if not self.outbox.guardar_en(db, evento.id, Topicos.EVENTOS, payload):
                raise sqlite3.IntegrityError(f"No se encoló el evento B1 {evento.id}")
            registro = db.execute("""
                INSERT INTO eventos_b1 (id, origen, equipo, nota_id, version, detalle, contexto)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (evento.id, evento.origen, evento.equipo, nota_id, version, evidencia, contexto))
            if registro.rowcount != 1:
                raise sqlite3.IntegrityError(f"No se guardó la evidencia B1 de {evento.id}")
            if evento.origen == "sensor":
                cambio = db.execute("""
                    UPDATE episodios_sensor_b1 SET publicacion = 'encolado'
                    WHERE id = ? AND publicacion = 'pendiente'
                """, (evento.id,))
                if cambio.rowcount != 1:
                    raise sqlite3.IntegrityError(f"No se actualizó el episodio B1 {evento.id}")
            return True

    def evidencia(self, id_evento: str) -> dict | None:
        with self._db() as db:
            fila = db.execute("SELECT detalle FROM eventos_b1 WHERE id = ?", (id_evento,)).fetchone()
        return json.loads(fila[0]) if fila else None

    def estado_publicacion(self, id_evento: str) -> str | None:
        with self._db() as db:
            fila = db.execute("""
                SELECT o.estado, o.intentos, o.ultimo_error
                FROM outbox o JOIN eventos_b1 e USING (id) WHERE o.id = ? AND o.es_b1 = 1
            """, (id_evento,)).fetchone()
            if fila is None:
                pendiente = db.execute("""
                    SELECT 1 FROM episodios_sensor_b1 WHERE id = ? AND publicacion = 'pendiente'
                """, (id_evento,)).fetchone()
                return "pendiente" if pendiente else None
        if fila["estado"] == "enviado":
            return "publicado"
        if fila["estado"] == "pendiente" and (
            fila["intentos"] >= self.outbox.MAX_INTENTOS or fila["ultimo_error"]
        ):
            return "fallo_tecnico"
        return "encolado"

    def siguiente_contexto(self):
        with self._db() as db:
            return db.execute("""
                SELECT id, detalle, intentos_contexto FROM eventos_b1
                WHERE contexto = 'pendiente' AND proximo_contexto <= ?
                ORDER BY proximo_contexto, rowid LIMIT 1
            """, (self.reloj(),)).fetchone()

    def contexto_sensor(self, equipo: str, hasta: datetime) -> dict | None:
        desde = hasta - timedelta(hours=3)
        with self._db() as db:
            fila = db.execute("""
                SELECT t.parte_id, t.version, t.filas, n.payload
                FROM decisiones_b1 d JOIN trabajos_b1 t USING (nota_id, version)
                JOIN notas n ON n.id = t.nota_id
                JOIN partes p ON p.nota_id = t.nota_id AND p.version = t.version
                WHERE d.equipo = ? AND p.estado = 'confirmado' AND t.estado = 'clasificado'
                  AND julianday(json_extract(n.payload, '$.ts')) BETWEEN julianday(?) AND julianday(?)
                ORDER BY julianday(json_extract(n.payload, '$.ts')) DESC, n.id LIMIT 1
            """, (equipo, desde.isoformat(), hasta.isoformat())).fetchone()
        if fila is None:
            return None
        nota = NotaRecorrida.model_validate_json(fila["payload"])
        return {
            "parte_id": fila["parte_id"], "version": fila["version"], "nota_id": nota.id,
            "ts": nota.ts.isoformat(), "filas": [f for f in json.loads(fila["filas"]) if f["tag"] == equipo],
            "fotos": referencias_fotos(nota),
        }

    def guardar_contexto(
        self, fila: sqlite3.Row, detalle: dict, fotos: list[tuple[str, str, bytes]], *, reintentar: bool,
    ) -> bool:
        intentos = fila["intentos_contexto"] + 1
        reprogramar = reintentar and intentos < self.MAX_INTENTOS_CONTEXTO
        estado = "pendiente" if reprogramar else "parcial" if reintentar else "registrado"
        proximo = self.reloj() + 2 ** intentos if reprogramar else 0
        evidencia = json.dumps(detalle, ensure_ascii=False, allow_nan=False)
        id_evento = fila["id"]
        with self._db() as db:
            cambio = db.execute("""
                UPDATE eventos_b1 SET detalle = ?, contexto = ?, intentos_contexto = ?, proximo_contexto = ?
                WHERE id = ? AND contexto = 'pendiente' AND intentos_contexto = ?
            """, (evidencia, estado, intentos, proximo, id_evento, fila["intentos_contexto"]))
            if cambio.rowcount != 1:
                return False
            insercion = db.executemany("""
                INSERT INTO fotos_evento_b1 (evento_id, ruta, mime, contenido) VALUES (?, ?, ?, ?)
            """, [(id_evento, *foto) for foto in fotos])
            if insercion.rowcount != len(fotos):
                raise sqlite3.IntegrityError(f"No se guardaron todas las fotos B1 de {id_evento}")
            return True
