"""Trabajos duraderos y decisiones B1 de partes confirmados."""

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from sistema.part_classification import DecisionB1


def crear_tablas_b1(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS trabajos_b1 (
            nota_id TEXT NOT NULL REFERENCES notas(id),
            parte_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            mensaje_id TEXT NOT NULL REFERENCES borradores_parte(mensaje_id),
            respuesta_id TEXT NOT NULL UNIQUE REFERENCES respuestas_parte(id),
            filas TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente'
                CHECK (estado IN ('pendiente', 'procesando', 'clasificado', 'fallido')),
            intentos INTEGER NOT NULL DEFAULT 0,
            disponible_desde REAL NOT NULL DEFAULT 0,
            ultimo_error TEXT,
            huella_catalogo TEXT,
            PRIMARY KEY (nota_id, version)
        )
    """)
    if not any(columna[1] == "huella_catalogo" for columna in db.execute("PRAGMA table_info(trabajos_b1)")):
        db.execute("ALTER TABLE trabajos_b1 ADD COLUMN huella_catalogo TEXT")
    db.execute("""
        CREATE INDEX IF NOT EXISTS trabajos_b1_disponibles
        ON trabajos_b1 (disponible_desde) WHERE estado IN ('pendiente', 'procesando')
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS decisiones_b1 (
            nota_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            equipo TEXT NOT NULL,
            resultado TEXT NOT NULL CHECK (resultado IN ('SinNovedad', 'Escalado', 'Indeterminado')),
            evidencia TEXT NOT NULL,
            PRIMARY KEY (nota_id, version, equipo),
            FOREIGN KEY (nota_id, version) REFERENCES trabajos_b1(nota_id, version)
        )
    """)


def registrar_confirmacion_b1(
    db: sqlite3.Connection, nota_id: str, version: int, mensaje_id: str,
    respuesta_id: str, filas: str,
) -> None:
    insercion = db.execute("""
        INSERT INTO trabajos_b1 (nota_id, parte_id, version, mensaje_id, respuesta_id, filas)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (nota_id, f"PARTE-{nota_id}", version, mensaje_id, respuesta_id, filas))
    if insercion.rowcount != 1:
        raise sqlite3.IntegrityError(f"No se registró el trabajo B1 de {nota_id}, versión {version}")


@dataclass(frozen=True)
class TrabajoB1:
    nota_id: str
    version: int
    intento: int
    filas_json: str

    @property
    def reserva(self) -> tuple[str, int, int]:
        return self.nota_id, self.version, self.intento


class AlmacenClasificacionB1:
    MAX_INTENTOS = 3
    RESERVA_S = 30

    def __init__(self, ruta: Path, *, reloj: Callable[[], float] = time.time):
        self.ruta = ruta
        self.reloj = reloj

    def reclamar(self) -> TrabajoB1 | None:
        ahora = self.reloj()
        with closing(sqlite3.connect(self.ruta)) as db, db:
            fila = db.execute("""
                SELECT nota_id, version, filas, intentos
                FROM trabajos_b1
                WHERE estado IN ('pendiente', 'procesando') AND disponible_desde <= ?
                ORDER BY disponible_desde, rowid LIMIT 1
            """, (ahora,)).fetchone()
            if fila is None:
                return None
            nota_id, version, filas, intentos = fila
            agotado = intentos >= self.MAX_INTENTOS
            # Cambiar la reserva solo si otro consumidor no tomó la misma versión.
            cambio = db.execute("""
                UPDATE trabajos_b1
                SET estado = ?, intentos = ?, disponible_desde = ?,
                    ultimo_error = COALESCE(?, ultimo_error)
                WHERE nota_id = ? AND version = ? AND intentos = ?
                  AND estado IN ('pendiente', 'procesando') AND disponible_desde <= ?
            """, (
                "fallido" if agotado else "procesando",
                intentos if agotado else intentos + 1,
                0 if agotado else ahora + self.RESERVA_S,
                "Clasificación interrumpida en el último intento" if agotado else None,
                nota_id, version, intentos, ahora,
            ))
            if agotado or cambio.rowcount != 1:
                return None
            return TrabajoB1(nota_id, version, intentos + 1, filas)

    def completar(
        self, trabajo: TrabajoB1, decisiones: tuple[DecisionB1, ...], *, huella_catalogo: str | None = None,
    ) -> bool:
        registros = [
            (trabajo.nota_id, trabajo.version, d.equipo, d.resultado,
             json.dumps([asdict(e) for e in d.evidencia], ensure_ascii=False, allow_nan=False))
            for d in decisiones
        ]
        with closing(sqlite3.connect(self.ruta)) as db, db:
            cierre = db.execute("""
                UPDATE trabajos_b1
                SET estado = 'clasificado', disponible_desde = 0, ultimo_error = NULL, huella_catalogo = ?
                WHERE nota_id = ? AND version = ? AND intentos = ? AND estado = 'procesando'
            """, (huella_catalogo, *trabajo.reserva))
            if cierre.rowcount != 1:
                return False
            insercion = db.executemany("""
                INSERT INTO decisiones_b1 (nota_id, version, equipo, resultado, evidencia)
                VALUES (?, ?, ?, ?, ?)
            """, registros)
            if insercion.rowcount != len(registros):
                raise sqlite3.IntegrityError(f"No se guardaron todas las decisiones B1 de {trabajo.nota_id}")
            return True

    def fallar(self, trabajo: TrabajoB1, error: Exception) -> bool:
        agotado = trabajo.intento >= self.MAX_INTENTOS
        disponible = 0 if agotado else self.reloj() + 2 ** (trabajo.intento - 1)
        detalle = f"{type(error).__name__}: {error}"[:300]
        with closing(sqlite3.connect(self.ruta)) as db, db:
            cambio = db.execute("""
                UPDATE trabajos_b1 SET estado = ?, disponible_desde = ?, ultimo_error = ?
                WHERE nota_id = ? AND version = ? AND intentos = ? AND estado = 'procesando'
            """, ("fallido" if agotado else "pendiente", disponible, detalle, *trabajo.reserva))
            return cambio.rowcount == 1
