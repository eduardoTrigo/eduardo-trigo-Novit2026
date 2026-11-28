"""Persistencia de partes derivados de notas completadas."""

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from uuid import uuid4

from planta_contrato import FilaParte, MensajeTablet, NotaRecorrida, RespuestaOperador, Topicos
from pydantic import TypeAdapter

from sistema.execution_budget import ConsumoEjecucion, CorteEjecucion
from sistema.note_store import AlmacenNotas
from sistema.part_classification_store import crear_tablas_b1, registrar_confirmacion_b1
from sistema.part_extraction import normalizar_texto
from sistema.part_memory_store import crear_cola_memoria
from sistema.part_photos import MAX_BYTES_FOTOS_PARTE, revalidar_fotos
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
    interacciones: int = 0


@dataclass(frozen=True)
class TareaSTT:
    respuesta: RespuestaOperador
    intento: int


@dataclass(frozen=True)
class EstadoEjecucion:
    id: str
    consumo: ConsumoEjecucion
    estado: str
    motivo: str | None


class AlmacenPartes:
    MAX_INTERACCIONES = 3
    MAX_INTENTOS_STT = 3
    RESERVA_STT_S = 30

    def __init__(self, notas: AlmacenNotas, outbox: OutboxSeguridad):
        if notas.ruta != outbox.ruta or not outbox.borradores:
            raise ValueError("Los partes requieren el carril de borradores de la misma base")
        self.ruta = notas.ruta
        self.outbox = outbox

        with closing(sqlite3.connect(self.ruta)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE IF NOT EXISTS fotos_partes_blob (
                    sha256 TEXT PRIMARY KEY,
                    mime TEXT NOT NULL,
                    contenido BLOB NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS partes (
                    nota_id TEXT PRIMARY KEY REFERENCES notas(id),
                    estado TEXT NOT NULL,
                    filas TEXT NOT NULL,
                    mensaje_id TEXT UNIQUE,
                    version INTEGER NOT NULL DEFAULT 1,
                    contexto TEXT NOT NULL DEFAULT '{}',
                    interacciones INTEGER NOT NULL DEFAULT 0
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
                    contenido TEXT,
                    texto_stt TEXT,
                    intentos_stt INTEGER NOT NULL DEFAULT 0,
                    proximo_stt REAL NOT NULL DEFAULT 0,
                    error_stt TEXT
                )
            """)
            columnas_respuesta = {r[1] for r in db.execute("PRAGMA table_info(respuestas_parte)")}
            for campo, tipo in (
                ("contenido", "TEXT"), ("texto_stt", "TEXT"), ("error_stt", "TEXT"),
                ("intentos_stt", "INTEGER NOT NULL DEFAULT 0"),
                ("proximo_stt", "REAL NOT NULL DEFAULT 0"),
            ):
                if campo not in columnas_respuesta:
                    db.execute(f"ALTER TABLE respuestas_parte ADD COLUMN {campo} {tipo}")
            db.execute("""
                CREATE INDEX IF NOT EXISTS respuestas_parte_resultado
                ON respuestas_parte (resultado)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS respuestas_parte_contenido
                ON respuestas_parte (mensaje_id, contenido) WHERE resultado IS NOT NULL
            """)
            if "interacciones" not in columnas:
                db.execute("ALTER TABLE partes ADD COLUMN interacciones INTEGER NOT NULL DEFAULT 0")
                self._migrar_interacciones(db)
            db.execute("""
                CREATE TABLE IF NOT EXISTS ejecucion_partes (
                    nota_id TEXT PRIMARY KEY REFERENCES notas(id),
                    ejecucion_id TEXT NOT NULL,
                    vence REAL NOT NULL,
                    consumo TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    motivo TEXT
                )
            """)
            crear_tablas_b1(db)
            crear_cola_memoria(db)

    def _migrar_interacciones(self, db: sqlite3.Connection) -> None:
        db.execute("""
            UPDATE partes AS p SET interacciones = MIN(?, (
                SELECT MAX(p.version - 1, COALESCE(SUM(r.resultado IN (
                    'confirmada', 'rechazada', 'corregida', 'resuelta',
                    'limite_interacciones', 'conflicto_lecturas'
                )), 0)) + COALESCE(SUM(r.resultado = 'insuficiente'), 0)
                FROM borradores_parte b JOIN respuestas_parte r ON r.mensaje_id = b.mensaje_id
                WHERE b.nota_id = p.nota_id
            ))
        """, (self.MAX_INTERACCIONES,))
        db.execute("""
            UPDATE partes SET estado = 'revision_manual'
            WHERE estado IN ('aclaracion_pendiente', 'borrador_pendiente') AND interacciones >= ?
        """, (self.MAX_INTERACCIONES,))

    def siguiente(self, *, timeout: float = 5.0) -> tuple[NotaRecorrida, str | None] | None:
        with closing(sqlite3.connect(self.ruta, timeout=timeout)) as db:
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

    def reclamar_ejecucion(self, nota_id, mensaje_id, presupuesto):
        """Deja la reserva durable antes de iniciar trabajo que pueda interrumpirse."""
        with closing(sqlite3.connect(self.ruta, timeout=presupuesto.reserva_cierre_s)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            parte = db.execute("SELECT mensaje_id, estado, interacciones FROM partes WHERE nota_id = ?",
                               (nota_id,)).fetchone()
            if mensaje_id is None:
                if parte:
                    return None
            elif not parte or parte[0] != mensaje_id or parte[1] not in {"borrador_pendiente", "aclaracion_pendiente"}:
                return None
            previo = db.execute("SELECT vence, consumo, estado FROM ejecucion_partes WHERE nota_id = ?",
                                (nota_id,)).fetchone()
            ahora = time.time()
            consumo = ConsumoEjecucion.model_validate_json(previo[1]) if previo else ConsumoEjecucion()
            interrumpida = bool(previo and previo[2] == "en_curso")
            if interrumpida and previo[0] > ahora:
                return None
            if interrumpida:
                consumo.activo_ms = max(consumo.activo_ms, int(presupuesto.tiempo_s * 1000))
            if parte:
                consumo.interacciones = parte[2]
            identificador = uuid4().hex
            vence = ahora + max(0, presupuesto.tiempo_s - consumo.activo_ms / 1000) + presupuesto.reserva_cierre_s
            db.execute("""
                INSERT INTO ejecucion_partes VALUES (?, ?, ?, ?, 'en_curso', NULL)
                ON CONFLICT(nota_id) DO UPDATE SET ejecucion_id=excluded.ejecucion_id, vence=excluded.vence,
                    consumo=excluded.consumo, estado=excluded.estado, motivo=NULL
            """, (nota_id, identificador, vence, consumo.model_dump_json()))
            recuperacion = "interrumpida" if interrumpida else "reversion" if previo and previo[2] == "reintentable" else None
            return identificador, consumo, recuperacion

    def estado_ejecucion(self, nota_id: str) -> EstadoEjecucion | None:
        with closing(sqlite3.connect(self.ruta, timeout=0.25)) as db:
            fila = db.execute("SELECT ejecucion_id, consumo, estado, motivo FROM ejecucion_partes WHERE nota_id = ?",
                              (nota_id,)).fetchone()
        return EstadoEjecucion(fila[0], ConsumoEjecucion.model_validate_json(fila[1]), *fila[2:]) if fila else None

    @contextmanager
    def _escritura(self, ejecucion):
        normal = ejecucion is not None and ejecucion.motivo is None
        timeout = 5.0
        if ejecucion is not None:
            timeout = ejecucion.presupuesto.reserva_cierre_s
        if normal:
            ejecucion.verificar()
            timeout = min(timeout, ejecucion.restante())
        with closing(sqlite3.connect(self.ruta, timeout=timeout)) as db, db:
            if normal:
                db.set_progress_handler(lambda: int(ejecucion.cancelada() or ejecucion.restante() <= 0), 200)
            yield db
            if normal:
                ejecucion.verificar()

    @staticmethod
    def _cerrar_ejecucion(db, ejecucion, interacciones):
        if ejecucion is None:
            return
        ejecucion.consumo.interacciones = interacciones
        ejecucion.medir()
        cambio = db.execute("""
            UPDATE ejecucion_partes SET consumo=?, estado=?, motivo=?, vence=0
            WHERE nota_id=? AND ejecucion_id=? AND estado='en_curso'
        """, (ejecucion.consumo.model_dump_json(), ejecucion.estado, ejecucion.motivo,
              ejecucion.nota_id, ejecucion.id))
        if cambio.rowcount != 1:
            raise CorteEjecucion("reserva_perdida")

    def registrar_reversion(self, ejecucion):
        """Registra el trabajo invertido después de un rollback conocido."""
        ejecucion.estado, ejecucion.motivo = "reintentable", "persistencia_revertida"
        with self._escritura(ejecucion) as db:
            self._cerrar_ejecucion(db, ejecucion, ejecucion.consumo.interacciones)

    def guardar(
        self,
        nota: NotaRecorrida,
        estado: str,
        filas: list[FilaParte],
        mensaje: MensajeTablet | None,
        contexto: dict | None = None,
        *,
        ejecucion=None,
        archivos_fotos: dict[str, tuple[str, bytes]] | None = None,
    ) -> bool:
        filas_json = _FILAS.dump_json(filas).decode("utf-8")
        mensaje_id = mensaje.id if mensaje is not None else None
        contexto_json = json.dumps(contexto or {}, ensure_ascii=False)

        with self._escritura(ejecucion) as db:
            cursor = db.execute("""
                INSERT INTO partes (nota_id, estado, filas, mensaje_id, contexto)
                SELECT id, ?, ?, ?, ? FROM notas WHERE id = ? AND estado = 'completada'
                ON CONFLICT(nota_id) DO NOTHING
            """, (estado, filas_json, mensaje_id, contexto_json, nota.id))
            if cursor.rowcount != 1:
                return False
            if archivos_fotos:
                omitidas = self._guardar_fotos(db, archivos_fotos)
                if omitidas:
                    for foto in contexto["fotos"]:
                        if foto["sha256"] in omitidas:
                            foto.update(estado_descarga="omitida_limite", sha256=None, mime=None, bytes=None)
                    contexto_json = json.dumps(contexto, ensure_ascii=False)
                    db.execute("UPDATE partes SET contexto = ? WHERE nota_id = ?", (contexto_json, nota.id))
            if mensaje is not None:
                self._encolar_borrador(db, nota.id, 1, filas_json, contexto_json, mensaje)
            self._cerrar_ejecucion(db, ejecucion, 0)
            return True

    @staticmethod
    def _guardar_fotos(db, archivos):
        disponibles = MAX_BYTES_FOTOS_PARTE - db.execute(
            "SELECT COALESCE(SUM(length(contenido)), 0) FROM fotos_partes_blob"
        ).fetchone()[0]
        omitidas = set()
        for huella, (mime, contenido) in archivos.items():
            if db.execute("SELECT 1 FROM fotos_partes_blob WHERE sha256 = ?", (huella,)).fetchone():
                continue
            if len(contenido) > disponibles:
                omitidas.add(huella)
                continue
            db.execute("INSERT INTO fotos_partes_blob VALUES (?, ?, ?)", (huella, mime, contenido))
            disponibles -= len(contenido)
        return omitidas

    def obtener_foto(self, sha256: str) -> tuple[str, bytes] | None:
        with closing(sqlite3.connect(self.ruta)) as db:
            return db.execute("SELECT mime, contenido FROM fotos_partes_blob WHERE sha256 = ?", (sha256,)).fetchone()

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

    def obtener(self, nota_id: str, *, timeout: float = 5.0) -> ParteGuardado | None:
        with closing(sqlite3.connect(self.ruta, timeout=timeout)) as db:
            fila = db.execute("""
                SELECT notas.payload, partes.estado, partes.filas, partes.mensaje_id,
                       partes.version, partes.contexto, partes.interacciones
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
            interacciones=fila[6],
        )

    def buscar_por_mensaje(self, mensaje_id: str, *, timeout: float = 5.0) -> ParteGuardado | None:
        with closing(sqlite3.connect(self.ruta, timeout=timeout)) as db:
            fila = db.execute(
                "SELECT nota_id FROM borradores_parte WHERE mensaje_id = ?", (mensaje_id,)
            ).fetchone()
        return self.obtener(fila[0], timeout=timeout) if fila else None

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

    def siguiente_respuesta(self, *, timeout: float = 5.0) -> tuple[RespuestaOperador, str | None] | None:
        with closing(sqlite3.connect(self.ruta, timeout=timeout)) as db:
            fila = db.execute("""
                SELECT payload, resultado, texto_stt FROM respuestas_parte
                WHERE resultado IS NULL OR resultado IN ('stt_listo', 'stt_agotado')
                ORDER BY rowid LIMIT 1
            """).fetchone()
        if fila is None:
            return None
        payload, fase, texto_stt = fila
        respuesta = RespuestaOperador.model_validate_json(payload, strict=True)
        if fase == "stt_listo":
            respuesta = respuesta.model_copy(update={"texto": texto_stt})
        return respuesta, fase

    def reclamar_stt(self) -> TareaSTT | None:
        ahora = time.time()
        with closing(sqlite3.connect(self.ruta)) as db, db:
            fila = db.execute("""
                SELECT id, payload, intentos_stt FROM respuestas_parte
                WHERE resultado IN ('requiere_stt', 'stt_procesando') AND proximo_stt <= ?
                ORDER BY proximo_stt, rowid LIMIT 1
            """, (ahora,)).fetchone()
            if fila is None:
                return None
            identificador, payload, intentos = fila
            agotado = intentos >= self.MAX_INTENTOS_STT
            cambio = db.execute("""
                UPDATE respuestas_parte
                SET resultado = ?, intentos_stt = ?, proximo_stt = ?,
                    error_stt = COALESCE(?, error_stt)
                WHERE id = ? AND resultado IN ('requiere_stt', 'stt_procesando')
                  AND intentos_stt = ? AND proximo_stt <= ?
            """, (
                "stt_agotado" if agotado else "stt_procesando",
                intentos if agotado else intentos + 1,
                0 if agotado else ahora + self.RESERVA_STT_S,
                "STT interrumpido en el último intento" if agotado else None,
                identificador, intentos, ahora,
            ))
            if agotado or cambio.rowcount != 1:
                return None
            return TareaSTT(RespuestaOperador.model_validate_json(payload, strict=True), intentos + 1)

    def finalizar_stt(
        self, tarea: TareaSTT, *, texto: str | None = None, error: Exception | None = None,
    ) -> bool:
        intentos = tarea.intento
        espera = 0.0
        if texto is not None:
            fase = "stt_listo"
        elif error is None:
            fase, intentos, espera = "requiere_stt", intentos - 1, 0.5
        elif intentos >= self.MAX_INTENTOS_STT or isinstance(error, ValueError):
            fase = "stt_agotado"
        else:
            fase, espera = "requiere_stt", 2 ** (intentos - 1)
        detalle = f"{type(error).__name__}: {error}"[:300] if error is not None else None
        with closing(sqlite3.connect(self.ruta)) as db, db:
            cambio = db.execute("""
                UPDATE respuestas_parte SET resultado = ?, texto_stt = ?, intentos_stt = ?,
                    proximo_stt = ?, error_stt = COALESCE(?, error_stt)
                WHERE id = ? AND resultado = 'stt_procesando' AND intentos_stt = ?
            """, (fase, texto, intentos, time.time() + espera, detalle, tarea.respuesta.id, tarea.intento))
            return cambio.rowcount == 1

    @staticmethod
    def _turno(db, respuesta, fase):
        return db.execute("""
                SELECT p.nota_id, p.estado, p.mensaje_id, p.version, p.filas, p.contexto, p.interacciones,
                       EXISTS (
                           SELECT 1 FROM respuestas_parte otras
                           WHERE otras.mensaje_id = r.mensaje_id AND otras.contenido = r.contenido
                             AND otras.resultado IS NOT NULL
                             AND otras.rowid < r.rowid
                             AND otras.resultado NOT IN ('duplicada', 'desfasada', 'mensaje_desconocido')
                       )
                FROM respuestas_parte r
                LEFT JOIN borradores_parte b ON b.mensaje_id = r.mensaje_id
                LEFT JOIN partes p ON p.nota_id = b.nota_id
                WHERE r.id = ? AND r.mensaje_id = ? AND r.resultado IS ?
        """, (respuesta.id, respuesta.mensaje_id, fase)).fetchone()

    def respuesta_ignorable(self, respuesta, fase, *, timeout=0.25):
        with closing(sqlite3.connect(self.ruta, timeout=timeout)) as db:
            turno = self._turno(db, respuesta, fase)
        return not turno or not turno[0] or turno[2] != respuesta.mensaje_id or turno[7] or turno[1] not in {
            "borrador_pendiente", "aclaracion_pendiente",
        }

    def aplicar_respuesta(
        self, respuesta: RespuestaOperador, resultado: str, mensaje: MensajeTablet | None = None,
        *, nuevo_estado: str | None = None, filas: list[FilaParte] | None = None,
        contexto: dict | None = None, fase: str | None = None, ejecucion=None,
    ) -> bool:
        """Revalida el turno y registra el resultado y sus escrituras en un único commit."""
        with self._escritura(ejecucion) as db:
            db.execute("BEGIN IMMEDIATE")
            fila = self._turno(db, respuesta, fase)
            if fila is None:
                return False
            nota_id, estado, vigente, version, filas_json, contexto_json, interacciones, duplicada = fila
            actualizacion = None
            if nota_id is None:
                resultado = "mensaje_desconocido"
            elif vigente != respuesta.mensaje_id or estado not in {"borrador_pendiente", "aclaracion_pendiente"}:
                resultado = "desfasada"
            elif duplicada:
                resultado = "duplicada"
            elif resultado != "requiere_stt":
                if resultado == "confirmada" and estado != "borrador_pendiente":
                    resultado = "insuficiente"
                interacciones += 1
                limite = interacciones >= self.MAX_INTERACCIONES and (mensaje is not None or resultado == "insuficiente")
                if contexto is not None:
                    if limite and "fotos" in contexto:
                        contexto = contexto | {"fotos": json.loads(contexto_json).get("fotos", [])}
                    contexto_json = json.dumps(contexto, ensure_ascii=False)
                if limite:
                    nuevo_estado = "revision_manual"
                    resultado = "limite_interacciones"
                elif resultado in {"confirmada", "rechazada"}:
                    nuevo_estado = "confirmado" if resultado == "confirmada" else "rechazado"
                elif resultado == "stt_fallido":
                    nuevo_estado = "revision_manual"
                else:
                    if filas is not None:
                        filas_json = _FILAS.dump_json(filas).decode("utf-8")
                        datos = contexto if contexto is not None else json.loads(contexto_json)
                        revalidar_fotos(datos, filas)
                        contexto_json = json.dumps(datos, ensure_ascii=False)
                    if mensaje is not None:
                        version += 1
                        vigente = mensaje.id
                        self._encolar_borrador(db, nota_id, version, filas_json, contexto_json, mensaje)
                actualizacion = db.execute("""
                    UPDATE partes SET estado = ?, version = ?, filas = ?, mensaje_id = ?, contexto = ?,
                        interacciones = ?
                    WHERE nota_id = ?
                """, (nuevo_estado or estado, version, filas_json, vigente, contexto_json, interacciones, nota_id))
            if actualizacion is not None and actualizacion.rowcount != 1:
                raise sqlite3.IntegrityError(f"No se actualizó el parte: {nota_id}")
            aplicada = db.execute(
                "UPDATE respuestas_parte SET resultado = ? WHERE id = ?", (resultado, respuesta.id)
            )
            if aplicada.rowcount != 1:
                raise sqlite3.IntegrityError(f"No se registró la respuesta: {respuesta.id}")
            if resultado == "confirmada":
                registrar_confirmacion_b1(db, nota_id, version, vigente, respuesta.id, filas_json)
            if ejecucion is not None and resultado == "limite_interacciones":
                ejecucion.estado = "revision_manual"
                ejecucion.motivo = "limite_interacciones"
            self._cerrar_ejecucion(db, ejecucion, interacciones or 0)
            return True
