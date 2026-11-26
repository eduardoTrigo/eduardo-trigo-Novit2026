"""Cola de revisiones y capturas consistentes de la conversación en SQLite."""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from planta_contrato import Topicos


_AHORA = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
_CIERRE = f"CASE WHEN NEW.estado IN ('confirmado', 'rechazado', 'sin_contenido') THEN {_AHORA} END"
_NOTA_DE_RESPUESTA = "(SELECT nota_id FROM borradores_parte WHERE mensaje_id = NEW.mensaje_id)"


@dataclass(frozen=True)
class CapturaMemoria:
    nota_id: str
    revision: int
    documento: dict


def crear_cola_memoria(db: sqlite3.Connection) -> None:
    """Instala la cola y sus disparadores en la transacción de esquema de partes."""
    existia = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memoria_pendiente'"
    ).fetchone()
    db.execute("""
        CREATE TABLE IF NOT EXISTS memoria_pendiente (
            nota_id TEXT PRIMARY KEY REFERENCES notas(id),
            revision INTEGER NOT NULL DEFAULT 1,
            sincronizada INTEGER NOT NULL DEFAULT 0,
            actualizada_en TEXT NOT NULL,
            resuelta_en TEXT
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS memoria_pendiente_orden
        ON memoria_pendiente (actualizada_en) WHERE revision > sincronizada
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS respuestas_parte_mensaje ON respuestas_parte (mensaje_id)
    """)
    # Las tablas operativas retienen los payloads. Los disparadores solo marcan la revisión.
    for nombre, evento, nota, cierre in (
        ("nota_nueva", "INSERT ON notas", "NEW.id", "NULL"),
        ("nota_actualizada", "UPDATE OF estado, intentos, transcripcion ON notas", "NEW.id", "NULL"),
        ("parte_nuevo", "INSERT ON partes", "NEW.nota_id", _CIERRE),
        ("parte_actualizado", "UPDATE ON partes", "NEW.nota_id", _CIERRE),
        ("respuesta_nueva", "INSERT ON respuestas_parte", _NOTA_DE_RESPUESTA, "NULL"),
        ("respuesta_actualizada", "UPDATE ON respuestas_parte", _NOTA_DE_RESPUESTA, "NULL"),
    ):
        db.execute(f"""
            CREATE TRIGGER IF NOT EXISTS memoria_{nombre} AFTER {evento}
            BEGIN
                INSERT INTO memoria_pendiente (nota_id, actualizada_en, resuelta_en)
                SELECT {nota}, {_AHORA}, {cierre} WHERE {nota} IS NOT NULL
                ON CONFLICT(nota_id) DO UPDATE SET
                    revision = memoria_pendiente.revision + 1,
                    actualizada_en = excluded.actualizada_en,
                    resuelta_en = COALESCE(memoria_pendiente.resuelta_en, excluded.resuelta_en);
            END
        """)
    if not existia:
        db.execute(f"""
            INSERT INTO memoria_pendiente (nota_id, actualizada_en)
            SELECT id, {_AHORA} FROM notas ORDER BY rowid
        """)


class AlmacenMemoriaLocal:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)

    def siguiente(self) -> CapturaMemoria | None:
        with closing(sqlite3.connect(self.ruta, timeout=2)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            pendiente = db.execute("""
                SELECT nota_id, revision, actualizada_en, resuelta_en
                FROM memoria_pendiente WHERE revision > sincronizada
                ORDER BY actualizada_en, rowid LIMIT 1
            """).fetchone()
            if pendiente is None:
                return None
            nota_id = pendiente["nota_id"]
            nota = db.execute("""
                SELECT payload, estado, intentos, actualizado, transcripcion FROM notas WHERE id = ?
            """, (nota_id,)).fetchone()
            parte = db.execute("""
                SELECT estado, version, mensaje_id, filas, contexto, interacciones
                FROM partes WHERE nota_id = ?
            """, (nota_id,)).fetchone()
            borradores = db.execute("""
                SELECT b.version, b.mensaje_id, b.filas, b.contexto, o.payload AS mensaje
                FROM borradores_parte b LEFT JOIN outbox o ON o.id = b.mensaje_id AND o.topico = ?
                WHERE b.nota_id = ? ORDER BY b.version
            """, (Topicos.TABLET, nota_id)).fetchall()
            respuestas = db.execute("""
                SELECT r.id, r.mensaje_id, r.payload, r.resultado, r.texto_stt, r.intentos_stt
                FROM borradores_parte b JOIN respuestas_parte r ON b.mensaje_id = r.mensaje_id
                WHERE b.nota_id = ? ORDER BY r.rowid
            """, (nota_id,)).fetchall()

        documento = {
            "nota": json.loads(nota["payload"]),
            "procesamiento_nota": {
                "estado": nota["estado"], "intentos_stt": nota["intentos"],
                "transcripcion": nota["transcripcion"], "actualizado_en": nota["actualizado"],
            },
            "parte": _decodificar(parte, "filas", "contexto") if parte else None,
            "borradores": [_decodificar(b, "filas", "contexto", "mensaje") for b in borradores],
            "respuestas": [_decodificar(r, "payload") for r in respuestas],
            "actualizada_en": pendiente["actualizada_en"],
            "resuelta_en": pendiente["resuelta_en"],
        }
        return CapturaMemoria(nota_id, pendiente["revision"], documento)

    def confirmar(self, captura: CapturaMemoria) -> None:
        with closing(sqlite3.connect(self.ruta, timeout=2)) as db, db:
            db.execute("""
                UPDATE memoria_pendiente SET sincronizada = ?
                WHERE nota_id = ? AND sincronizada < ? AND revision >= ?
            """, (captura.revision, captura.nota_id, captura.revision, captura.revision))

    def revision(self, nota_id: str) -> int | None:
        if not self.ruta.is_file():
            return None
        with closing(sqlite3.connect(self.ruta, timeout=2)) as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memoria_pendiente'"
            ).fetchone():
                return None
            fila = db.execute(
                "SELECT revision FROM memoria_pendiente WHERE nota_id = ?", (nota_id,)
            ).fetchone()
        return fila[0] if fila else None


def _decodificar(fila: sqlite3.Row, *campos_json: str) -> dict:
    valores = dict(fila)
    for campo in campos_json:
        if valores[campo] is not None:
            valores[campo] = json.loads(valores[campo])
    return valores
