
"""Almacenamiento persistente de notas PIA-3."""

import sqlite3
import time
from pathlib import Path

from planta_contrato import NotaRecorrida


class AlmacenNotas:
    """Persiste notas y controla sus reintentos."""

    MAX_INTENTOS = 3

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._crear_tabla()

    def _conectar(self):
        db = sqlite3.connect(self.ruta, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def _crear_tabla(self):
        with self._conectar() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS notas (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN (
                            'pendiente',
                            'procesando',
                            'completada'
                        )),
                    intentos INTEGER NOT NULL DEFAULT 0,
                    ultimo_error TEXT,
                    actualizado TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    proximo_intento REAL NOT NULL DEFAULT 0,
                    transcripcion TEXT
                )
            """)

            columnas = {
                fila["name"]
                for fila in db.execute(
                    "PRAGMA table_info(notas)"
                )
            }

            if "proximo_intento" not in columnas:
                db.execute("""
                    ALTER TABLE notas
                    ADD COLUMN proximo_intento
                    REAL NOT NULL DEFAULT 0
                """)

            if "transcripcion" not in columnas:
                db.execute("ALTER TABLE notas ADD COLUMN transcripcion TEXT")

    def guardar(self, nota: NotaRecorrida) -> bool:
        solo_evidencia = nota.id.startswith("VIVO-") and not nota.audio_url
        with self._conectar() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO notas (id, payload, estado)
                VALUES (?, ?, ?)
                """,
                (nota.id, nota.model_dump_json(), "completada" if solo_evidencia else "pendiente"),
            )
            return cursor.rowcount == 1

    def reclamar_siguiente(self) -> NotaRecorrida | None:
        """Reserva una nota elegible para procesamiento."""

        ahora = time.time()

        with self._conectar() as db:
            db.execute("BEGIN IMMEDIATE")

            fila = db.execute(
                """
                SELECT id, payload
                FROM notas
                WHERE estado = 'pendiente'
                  AND intentos < ?
                  AND proximo_intento <= ?
                ORDER BY proximo_intento, rowid
                LIMIT 1
                """,
                (self.MAX_INTENTOS, ahora),
            ).fetchone()

            if fila is None:
                return None

            db.execute(
                """
                UPDATE notas
                SET estado = 'procesando',
                    intentos = intentos + 1,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (fila["id"],),
            )

            return NotaRecorrida.model_validate_json(
                fila["payload"]
            )

    def completar(self, nota_id: str) -> bool:
        with self._conectar() as db:
            cursor = db.execute(
                """
                UPDATE notas
                SET estado = 'completada',
                    ultimo_error = NULL,
                    proximo_intento = 0,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ? AND estado = 'procesando'
                """,
                (nota_id,),
            )
            return cursor.rowcount == 1

    def reintentar(self, nota_id: str, error: str) -> bool:
        """Programa el próximo intento con espera progresiva."""

        with self._conectar() as db:
            db.execute("BEGIN IMMEDIATE")

            fila = db.execute(
                """
                SELECT intentos
                FROM notas
                WHERE id = ? AND estado = 'procesando'
                """,
                (nota_id,),
            ).fetchone()

            if fila is None:
                return False

            intentos = fila["intentos"]

            # 2 segundos, 4 segundos, 8 segundos...
            demora = min(2 ** intentos, 60)

            cursor = db.execute(
                """
                UPDATE notas
                SET estado = 'pendiente',
                    ultimo_error = ?,
                    proximo_intento = ?,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ? AND estado = 'procesando'
                """,
                (
                    error[:1000],
                    time.time() + demora,
                    nota_id,
                ),
            )

            # El límite se aplica en reclamar_siguiente().
            return cursor.rowcount == 1

    def recuperar_interrumpidas(self) -> int:
        """Devuelve a pendientes las tareas interrumpidas."""

        with self._conectar() as db:
            cursor = db.execute(
                """
                UPDATE notas
                SET estado = 'pendiente',
                    actualizado = CURRENT_TIMESTAMP
                WHERE estado = 'procesando'
                """
            )
            return cursor.rowcount

    def obtener_estado(self, nota_id: str) -> str | None:
        with self._conectar() as db:
            fila = db.execute(
                "SELECT estado FROM notas WHERE id = ?",
                (nota_id,),
            ).fetchone()

            return fila["estado"] if fila else None

    def obtener_transcripcion(self, nota_id: str) -> str | None:
        """Lee el texto duradero de una nota completada, si lo tiene."""
        with self._conectar() as db:
            fila = db.execute(
                """
                SELECT transcripcion
                FROM notas
                WHERE id = ? AND estado = 'completada'
                """,
                (nota_id,),
            ).fetchone()

            return fila["transcripcion"] if fila else None

    def pendientes(self) -> int:
        """Cuenta notas pendientes, incluso las bloqueadas."""

        with self._conectar() as db:
            fila = db.execute("""
                SELECT COUNT(*) AS total
                FROM notas
                WHERE estado = 'pendiente'
            """).fetchone()

            return fila["total"]

    def agotadas(self) -> int:
        """Cuenta notas que requieren intervención manual."""

        with self._conectar() as db:
            fila = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM notas
                WHERE estado = 'pendiente'
                  AND intentos >= ?
                """,
                (self.MAX_INTENTOS,),
            ).fetchone()

            return fila["total"]
