"""Persistencia aditiva de series y escalados internos de sensores."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from sistema.sensor_detection import Episodio, Punto, Serie, Transicion, interrumpir

_SERIE = TypeAdapter(Serie)
_EPISODIO = TypeAdapter(Episodio)
_PUNTOS = TypeAdapter(tuple[Punto, ...])


@dataclass(frozen=True)
class RegistroSerie:
    serie: Serie = Serie()
    revision: int = 0


class AlmacenSensoresB1:
    def __init__(self, ruta: Path, epoca: str):
        if not epoca.strip():
            raise ValueError("La época de sensores B1 no puede estar vacía")
        self.ruta = ruta
        self.epoca = epoca
        with closing(sqlite3.connect(ruta)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS series_sensor_b1 (
                    epoca TEXT NOT NULL,
                    equipo TEXT NOT NULL,
                    variable TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    estado TEXT NOT NULL,
                    interrupcion TEXT,
                    PRIMARY KEY (epoca, equipo, variable)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS episodios_sensor_b1 (
                    id TEXT PRIMARY KEY NOT NULL,
                    epoca TEXT NOT NULL,
                    equipo TEXT NOT NULL,
                    variable TEXT NOT NULL,
                    direccion TEXT NOT NULL,
                    inicio TEXT NOT NULL,
                    abierto TEXT NOT NULL,
                    huella_limites TEXT NOT NULL,
                    evidencia TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'Escalado',
                    publicacion TEXT NOT NULL DEFAULT 'pendiente',
                    recuperado TEXT,
                    recuperacion TEXT,
                    huella_recuperacion TEXT
                )
            """)

    def leer(self, clave: tuple[str, str]) -> RegistroSerie:
        with closing(sqlite3.connect(self.ruta)) as db:
            fila = db.execute("""
                SELECT estado, revision, interrupcion FROM series_sensor_b1
                WHERE epoca = ? AND equipo = ? AND variable = ?
            """, (self.epoca, *clave)).fetchone()
        if fila is None:
            return RegistroSerie()
        estado, revision, motivo = fila
        serie = _SERIE.validate_json(estado)
        return RegistroSerie(interrumpir(serie, motivo) if motivo else serie, revision)

    def interrumpir(self, motivo: str, clave: tuple[str, str] | None = None) -> None:
        sql = "UPDATE series_sensor_b1 SET interrupcion = ?, revision = revision + 1 WHERE epoca = ?"
        parametros = (motivo, self.epoca)
        if clave is not None:
            sql += " AND equipo = ? AND variable = ?"
            parametros += clave
        with closing(sqlite3.connect(self.ruta)) as db, db:
            db.execute(sql, parametros)

    def guardar(self, clave: tuple[str, str], anterior: RegistroSerie, cambio: Transicion) -> bool:
        if cambio.serie == anterior.serie:
            return True
        identidad = (self.epoca, *clave)
        estado = _SERIE.dump_json(cambio.serie).decode()
        evidencia = _EPISODIO.dump_json(cambio.apertura or cambio.cierre).decode() if (
            cambio.apertura or cambio.cierre
        ) else None
        recuperacion = _PUNTOS.dump_json(cambio.recuperacion).decode() if cambio.cierre else None

        with closing(sqlite3.connect(self.ruta)) as db, db:
            if anterior.revision == 0:
                actualizacion = db.execute("""
                    INSERT INTO series_sensor_b1 (epoca, equipo, variable, revision, estado)
                    VALUES (?, ?, ?, 1, ?) ON CONFLICT(epoca, equipo, variable) DO NOTHING
                """, (*identidad, estado))
            else:
                actualizacion = db.execute("""
                    UPDATE series_sensor_b1
                    SET estado = ?, revision = revision + 1, interrupcion = NULL
                    WHERE epoca = ? AND equipo = ? AND variable = ? AND revision = ?
                """, (estado, *identidad, anterior.revision))
            if actualizacion.rowcount != 1:
                return False

            if apertura := cambio.apertura:
                insercion = db.execute("""
                    INSERT INTO episodios_sensor_b1
                        (id, epoca, equipo, variable, direccion, inicio, abierto, huella_limites, evidencia)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (apertura.id, *identidad, apertura.direccion, apertura.inicio.isoformat(),
                      apertura.apertura.isoformat(), cambio.serie.huella, evidencia))
                if insercion.rowcount != 1:
                    raise sqlite3.IntegrityError(f"No se registró la apertura B1 de {clave}")
            if cierre := cambio.cierre:
                actualizacion = db.execute("""
                    UPDATE episodios_sensor_b1
                    SET recuperado = ?, recuperacion = ?, huella_recuperacion = ?, evidencia = ?
                    WHERE id = ? AND recuperado IS NULL
                """, (cambio.serie.ultimo.ts.isoformat(), recuperacion, cambio.serie.huella, evidencia, cierre.id))
                if actualizacion.rowcount != 1:
                    raise sqlite3.IntegrityError(f"No se registró el cierre B1 de {clave}")
            return True
