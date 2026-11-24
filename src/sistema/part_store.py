"""Persistencia de partes derivados de notas completadas."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field

from planta_contrato import FilaParte, MensajeTablet, NotaRecorrida, RespuestaOperador, Topicos
from pydantic import TypeAdapter

from sistema.note_store import AlmacenNotas
from sistema.part_extraction import normalizar_texto
from sistema.security_outbox import OutboxSeguridad


_FILAS = TypeAdapter(list[FilaParte])


@dataclass(frozen=True)
class ParteGuardado:
    nota: NotaRecorrida
    estado: str
    filas: list[FilaParte]
    mensaje_id: str | None
    version: int
    contexto: dict = field(default_factory=dict)


class AlmacenPartes:
    MAX_INTERACCIONES = 3

    def __init__(self, notas: AlmacenNotas, outbox: OutboxSeguridad):
        if notas.ruta != outbox.ruta or not outbox.borradores:
            raise ValueError("Los partes requieren el carril de borradores de la misma base")
        self.ruta = notas.ruta
        self.outbox = outbox

        with closing(sqlite3.connect(self.ruta)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS partes (
                    nota_id TEXT PRIMARY KEY REFERENCES notas(id),
                    estado TEXT NOT NULL,
                    filas TEXT NOT NULL,
                    mensaje_id TEXT UNIQUE,
                    version INTEGER NOT NULL DEFAULT 1,
                    contexto TEXT NOT NULL DEFAULT '{}'
                )
            """)
            columnas = {r[1] for r in db.execute("PRAGMA table_info(partes)")}
            if "version" not in columnas:
                db.execute("ALTER TABLE partes ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            if "contexto" not in columnas:
                db.execute("ALTER TABLE partes ADD COLUMN contexto TEXT NOT NULL DEFAULT '{}'")
            db.execute("""
                UPDATE partes SET estado = 'revision_manual'
                WHERE estado = 'aclaracion_pendiente'
                  AND (
                      mensaje_id IS NULL
                      OR COALESCE(json_array_length(contexto, '$.pendientes'), 0) = 0
                  )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS borradores_parte (
                    mensaje_id TEXT PRIMARY KEY,
                    nota_id TEXT NOT NULL REFERENCES partes(nota_id),
                    version INTEGER NOT NULL,
                    filas TEXT NOT NULL,
                    contexto TEXT NOT NULL DEFAULT '{}',
                    UNIQUE (nota_id, version)
                )
            """)
            if "contexto" not in {r[1] for r in db.execute("PRAGMA table_info(borradores_parte)")}:
                db.execute("ALTER TABLE borradores_parte ADD COLUMN contexto TEXT NOT NULL DEFAULT '{}'")
            db.execute("""
                INSERT INTO borradores_parte (mensaje_id, nota_id, version, filas, contexto)
                SELECT mensaje_id, nota_id, version, filas, contexto
                FROM partes WHERE mensaje_id IS NOT NULL
                ON CONFLICT(mensaje_id) DO NOTHING
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS respuestas_parte (
                    id TEXT PRIMARY KEY,
                    mensaje_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    resultado TEXT,
                    contenido TEXT
                )
            """)
            if "contenido" not in {r[1] for r in db.execute("PRAGMA table_info(respuestas_parte)")}:
                db.execute("ALTER TABLE respuestas_parte ADD COLUMN contenido TEXT")
            db.execute("""
                CREATE INDEX IF NOT EXISTS respuestas_parte_resultado
                ON respuestas_parte (resultado)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS respuestas_parte_contenido
                ON respuestas_parte (mensaje_id, contenido) WHERE resultado IS NOT NULL
            """)

    def siguiente(self) -> tuple[NotaRecorrida, str | None] | None:
        with closing(sqlite3.connect(self.ruta)) as db:
            fila = db.execute("""
                SELECT notas.payload, notas.transcripcion
                FROM notas
                WHERE notas.estado = 'completada'
                  AND (
                    notas.transcripcion IS NOT NULL
                    OR (
                        notas.id GLOB 'VIVO-*'
                        AND COALESCE(json_extract(notas.payload, '$.audio_url'), '') = ''
                    )
                  )
                  AND NOT EXISTS (SELECT 1 FROM partes WHERE partes.nota_id = notas.id)
                ORDER BY notas.rowid
                LIMIT 1
            """).fetchone()
        if fila is None:
            return None
        return NotaRecorrida.model_validate_json(fila[0]), fila[1]

    def guardar(
        self,
        nota: NotaRecorrida,
        estado: str,
        filas: list[FilaParte],
        mensaje: MensajeTablet | None,
        contexto: dict | None = None,
    ) -> bool:
        filas_json = _FILAS.dump_json(filas).decode("utf-8")
        mensaje_id = mensaje.id if mensaje is not None else None
        contexto_json = json.dumps(contexto or {}, ensure_ascii=False)

        with closing(sqlite3.connect(self.ruta)) as db, db:
            cursor = db.execute("""
                INSERT INTO partes (nota_id, estado, filas, mensaje_id, contexto)
                SELECT id, ?, ?, ?, ? FROM notas WHERE id = ? AND estado = 'completada'
                ON CONFLICT(nota_id) DO NOTHING
            """, (estado, filas_json, mensaje_id, contexto_json, nota.id))
            if cursor.rowcount != 1:
                return False
            if mensaje is not None:
                self._encolar_borrador(db, nota.id, 1, filas_json, contexto_json, mensaje)
            return True

    def _encolar_borrador(
        self, db: sqlite3.Connection, nota_id: str, version: int, filas_json: str,
        contexto_json: str, mensaje: MensajeTablet,
    ) -> None:
        borrador = db.execute("""
            INSERT INTO borradores_parte (mensaje_id, nota_id, version, filas, contexto)
            VALUES (?, ?, ?, ?, ?)
        """, (mensaje.id, nota_id, version, filas_json, contexto_json))
        if borrador.rowcount != 1 or not self.outbox.guardar_en(
            db, mensaje.id, Topicos.TABLET, mensaje.model_dump_json()
        ):
            raise sqlite3.IntegrityError(f"Conflicto de mensaje de parte: {mensaje.id}")

    def obtener(self, nota_id: str) -> ParteGuardado | None:
        with closing(sqlite3.connect(self.ruta)) as db:
            fila = db.execute("""
                SELECT notas.payload, partes.estado, partes.filas, partes.mensaje_id,
                       partes.version, partes.contexto
                FROM partes JOIN notas ON notas.id = partes.nota_id
                WHERE partes.nota_id = ?
            """, (nota_id,)).fetchone()
        if fila is None:
            return None
        return ParteGuardado(
            nota=NotaRecorrida.model_validate_json(fila[0]),
            estado=fila[1],
            filas=_FILAS.validate_json(fila[2]),
            mensaje_id=fila[3],
            version=fila[4],
            contexto=json.loads(fila[5]),
        )

    def buscar_por_mensaje(self, mensaje_id: str) -> ParteGuardado | None:
        with closing(sqlite3.connect(self.ruta)) as db:
            fila = db.execute(
                "SELECT nota_id FROM borradores_parte WHERE mensaje_id = ?", (mensaje_id,)
            ).fetchone()
        return self.obtener(fila[0]) if fila else None

    def guardar_respuesta(self, respuesta: RespuestaOperador) -> bool:
        contenido = json.dumps([
            respuesta.operador, " ".join(normalizar_texto(respuesta.texto).split()),
            respuesta.audio_url,
        ], default=str)
        with closing(sqlite3.connect(self.ruta)) as db, db:
            cursor = db.execute("""
                INSERT INTO respuestas_parte (id, mensaje_id, payload, contenido)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
            """, (respuesta.id, respuesta.mensaje_id, respuesta.model_dump_json(), contenido))
            return cursor.rowcount == 1

    def siguiente_respuesta(self) -> RespuestaOperador | None:
        with closing(sqlite3.connect(self.ruta)) as db:
            fila = db.execute("""
                SELECT payload FROM respuestas_parte
                WHERE resultado IS NULL ORDER BY rowid LIMIT 1
            """).fetchone()
        return RespuestaOperador.model_validate_json(fila[0], strict=True) if fila else None

    def aplicar_respuesta(
        self, respuesta: RespuestaOperador, resultado: str, mensaje: MensajeTablet | None = None,
        *, nuevo_estado: str | None = None, filas: list[FilaParte] | None = None,
        contexto: dict | None = None,
    ) -> bool:
        """Revalida el turno y registra el resultado y sus escrituras en un único commit."""
        with closing(sqlite3.connect(self.ruta)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            fila = db.execute("""
                SELECT p.nota_id, p.estado, p.mensaje_id, p.version, p.filas, p.contexto,
                       EXISTS (
                           SELECT 1 FROM respuestas_parte otras
                           WHERE otras.mensaje_id = r.mensaje_id AND otras.contenido = r.contenido
                             AND otras.resultado IS NOT NULL
                       )
                FROM respuestas_parte r
                LEFT JOIN borradores_parte b ON b.mensaje_id = r.mensaje_id
                LEFT JOIN partes p ON p.nota_id = b.nota_id
                WHERE r.id = ? AND r.mensaje_id = ? AND r.resultado IS NULL
            """, (respuesta.id, respuesta.mensaje_id)).fetchone()
            if fila is None:
                return False
            nota_id, estado, vigente, version, filas_json, contexto_json, duplicada = fila
            actualizacion = None
            if nota_id is None:
                resultado = "mensaje_desconocido"
            elif vigente != respuesta.mensaje_id or estado not in {"borrador_pendiente", "aclaracion_pendiente"}:
                resultado = "desfasada"
            elif duplicada:
                resultado = "duplicada"
            else:
                if contexto is not None:
                    contexto_json = json.dumps(contexto, ensure_ascii=False)
                if mensaje is not None and version >= self.MAX_INTERACCIONES:
                    nuevo_estado = "revision_manual"
                    resultado = "limite_interacciones"
                elif resultado in {"confirmada", "rechazada"}:
                    nuevo_estado = "confirmado" if resultado == "confirmada" else "rechazado"
                elif resultado in {"insuficiente", "requiere_stt"} and version >= self.MAX_INTERACCIONES:
                    nuevo_estado = "revision_manual"
                    resultado = "limite_interacciones"
                else:
                    if filas is not None:
                        filas_json = _FILAS.dump_json(filas).decode("utf-8")
                    if mensaje is not None:
                        version += 1
                        vigente = mensaje.id
                        self._encolar_borrador(db, nota_id, version, filas_json, contexto_json, mensaje)
                if nuevo_estado is not None or mensaje is not None or contexto is not None:
                    actualizacion = db.execute("""
                        UPDATE partes SET estado = ?, version = ?, filas = ?, mensaje_id = ?, contexto = ?
                        WHERE nota_id = ?
                    """, (nuevo_estado or estado, version, filas_json, vigente, contexto_json, nota_id))
            if actualizacion is not None and actualizacion.rowcount != 1:
                raise sqlite3.IntegrityError(f"No se actualizó el parte: {nota_id}")
            aplicada = db.execute(
                "UPDATE respuestas_parte SET resultado = ? WHERE id = ?", (resultado, respuesta.id)
            )
            if aplicada.rowcount != 1:
                raise sqlite3.IntegrityError(f"No se registró la respuesta: {respuesta.id}")
            return True
