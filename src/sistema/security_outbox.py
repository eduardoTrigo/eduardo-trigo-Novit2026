
"""Outbox persistente de seguridad para PIA-3.

Implementa reintentos controlados y recuperación
de publicaciones MQTT interrumpidas.
"""

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class OutboxSeguridad:
    MAX_INTENTOS = 3

    def __init__(self, ruta: str | Path, *, borradores: bool = False):
        self.ruta = Path(ruta)
        self.borradores = borradores
        self.ruta.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY,
                    topico TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK (estado IN (
                            'pendiente',
                            'enviando',
                            'enviado'
                        )),
                    intentos INTEGER NOT NULL DEFAULT 0,
                    ultimo_error TEXT,
                    actualizado TEXT NOT NULL
                        DEFAULT CURRENT_TIMESTAMP,
                    proximo_intento REAL NOT NULL DEFAULT 0,
                    es_borrador INTEGER NOT NULL DEFAULT 0
                )
            """)

            # Migrar bases existentes sin eliminar datos.
            columnas = {
                fila["name"]
                for fila in db.execute(
                    "PRAGMA table_info(outbox)"
                )
            }

            if "proximo_intento" not in columnas:
                db.execute("""
                    ALTER TABLE outbox
                    ADD COLUMN proximo_intento
                    REAL NOT NULL DEFAULT 0
                """)

            if "es_borrador" not in columnas:
                db.execute("""
                    ALTER TABLE outbox ADD COLUMN es_borrador INTEGER NOT NULL DEFAULT 0
                """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(
            self.ruta,
            timeout=5,
        )
        db.row_factory = sqlite3.Row

        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def guardar(
        self,
        id: str,
        topico: str,
        payload: str,
    ) -> bool:
        """Guarda un mensaje sin duplicar su ID."""

        with self._db() as db:
            return self.guardar_en(db, id, topico, payload)

    def guardar_en(self, db: sqlite3.Connection, id: str, topico: str, payload: str) -> bool:
        """Inserta en el carril de esta instancia dentro de una transacción externa."""
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO outbox (id, topico, payload, es_borrador)
            VALUES (?, ?, ?, ?)
            """,
            (id, topico, payload, self.borradores),
        )
        return cursor.rowcount == 1

    def reclamar(self) -> dict | None:
        """Reserva un mensaje elegible para publicar."""

        ahora = time.time()

        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")

            fila = db.execute(
                """
                SELECT id, topico, payload
                FROM outbox
                WHERE estado = 'pendiente'
                  AND es_borrador = ?
                  AND intentos < ?
                  AND proximo_intento <= ?
                ORDER BY proximo_intento, rowid
                LIMIT 1
                """,
                (self.borradores, self.MAX_INTENTOS, ahora),
            ).fetchone()

            if fila is None:
                return None

            db.execute(
                """
                UPDATE outbox
                SET estado = 'enviando',
                    intentos = intentos + 1,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (fila["id"],),
            )

            return dict(fila)

    def confirmar(self, id: str) -> bool:
        """Confirma una publicación reconocida por MQTT."""

        with self._db() as db:
            cursor = db.execute(
                """
                UPDATE outbox
                SET estado = 'enviado',
                    ultimo_error = NULL,
                    proximo_intento = 0,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND estado = 'enviando'
                  AND es_borrador = ?
                """,
                (id, self.borradores),
            )

            return cursor.rowcount == 1

    def fallar(self, id: str, error: str) -> bool:
        """Programa un reintento o bloquea el envío agotado."""

        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")

            fila = db.execute(
                """
                SELECT intentos
                FROM outbox
                WHERE id = ?
                  AND estado = 'enviando'
                  AND es_borrador = ?
                """,
                (id, self.borradores),
            ).fetchone()

            if fila is None:
                return False

            intentos = fila["intentos"]

            # Espera progresiva: 2, 4, 8... segundos.
            demora = min(2 ** intentos, 60)

            cursor = db.execute(
                """
                UPDATE outbox
                SET estado = 'pendiente',
                    ultimo_error = ?,
                    proximo_intento = ?,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND estado = 'enviando'
                """,
                (
                    str(error)[:1000],
                    time.time() + demora,
                    id,
                ),
            )

            if intentos >= self.MAX_INTENTOS:
                logger.error(
                    "ALERTA AGOTADA | %s | %s intentos | %s",
                    id,
                    intentos,
                    error,
                )

            return cursor.rowcount == 1

    def recuperar(self) -> int:
        """Recupera publicaciones interrumpidas.

        Solo debe ejecutarse cuando no hay otro publicador en el mismo carril.
        """

        with self._db() as db:
            cursor = db.execute(
                """
                UPDATE outbox
                SET estado = 'pendiente',
                    actualizado = CURRENT_TIMESTAMP
                WHERE estado = 'enviando'
                  AND es_borrador = ?
                """,
                (self.borradores,),
            )

            return cursor.rowcount

    def pendientes(self) -> int:
        """Cuenta todos los mensajes sin enviar."""

        with self._db() as db:
            fila = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM outbox
                WHERE estado = 'pendiente'
                  AND es_borrador = ?
                """,
                (self.borradores,),
            ).fetchone()

            return fila["total"]

    def agotadas(self) -> int:
        """Cuenta alertas que requieren intervención."""

        with self._db() as db:
            fila = db.execute(
                """
                SELECT COUNT(*) AS total
                FROM outbox
                WHERE estado = 'pendiente'
                  AND es_borrador = ?
                  AND intentos >= ?
                """,
                (self.borradores, self.MAX_INTENTOS),
            ).fetchone()

            return fila["total"]

    def reactivar(self, id: str) -> bool:
        """Rehabilita manualmente un mensaje agotado.

        Utilizar solo después de revisar la causa
        del fallo y el riesgo de publicación duplicada.
        """

        with self._db() as db:
            cursor = db.execute(
                """
                UPDATE outbox
                SET intentos = 0,
                    proximo_intento = 0,
                    ultimo_error = NULL,
                    actualizado = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND estado = 'pendiente'
                  AND es_borrador = ?
                  AND intentos >= ?
                """,
                (id, self.borradores, self.MAX_INTENTOS),
            )

            return cursor.rowcount == 1
