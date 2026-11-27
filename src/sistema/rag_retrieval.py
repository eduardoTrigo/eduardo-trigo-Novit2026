"""Recuperación documental de solo lectura sobre un corpus PIA-3 identificado."""

import json
import os
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import Field, FiniteFloat, StringConstraints, field_validator

from sistema.plant_tool_models import Codigo, ErrorTool, Estricto, Sha256
from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres, conectar_postgres
from sistema.rag_corpus import Aplicabilidad
from sistema.rag_embeddings import ModeloEmbedding


MAX_RESULTADOS = 20
MAX_CANDIDATOS = 100
MAX_TEXTO = 3000
TIMEOUT_SQL_MS = 2500
_CORPUS_ID = re.compile(r"[0-9a-f]{64}")
_CODIGOS = re.compile(r"(?<![\w-])(?:[a-z0-9]+(?:-[a-z0-9]+)+|f(?:0[1-9]|10))(?![\w-])", re.I | re.ASCII)
_CITAS = re.compile(r"\b([a-z0-9]+(?:-[a-z0-9]+)+)\s*§\s*((?:[a-z]|[0-9]+)(?:\.[0-9]+)*)(?!\w|\.[0-9])",
                    re.I | re.ASCII)
_OT = re.compile(r"ot-[0-9]{2}-[0-9]{4}")
_ALIAS = (
    (re.compile(r"\b(?:cbio|cambio)\b", re.I), "cbio cambio"),
    (re.compile(r"\b(?:empaq|empaquetadura)\b", re.I), "empaq empaquetadura"),
    (re.compile(r"\b(?:dp|Δp|delta\s+p|presi[oó]n\s+diferencial)\b", re.I), "ΔP presión diferencial"),
)
TipoDocumento = Literal["manual_fabricante", "manual_operadora", "contrato", "historial"]
DocumentoId = Codigo | Literal["historial"]


class FiltrosManuales(Estricto):
    equipo: Codigo | None = None
    fabricante: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    modelo: Codigo | None = None
    tipo_documento: TipoDocumento | None = None
    documento: DocumentoId | None = None
    vigencia: Literal["operativa", "vigente", "todas", "obsoleta"] = "operativa"
    aplicabilidad: Literal["confirmada", "indeterminada"] | None = None

    @field_validator("fabricante")
    @classmethod
    def validar_fabricante(cls, valor):
        if valor is not None and not valor.isprintable():
            raise ValueError("El fabricante debe ser texto imprimible")
        return valor


class ConsultaManuales(Estricto):
    consulta: Annotated[str, Field(min_length=1, max_length=400)]
    filtros: FiltrosManuales = Field(default_factory=FiltrosManuales)
    top_k: Annotated[int, Field(ge=1, le=MAX_RESULTADOS)] = 5

    @field_validator("consulta")
    @classmethod
    def validar_consulta(cls, valor):
        if any(not c.isprintable() and c not in "\t\r\n" for c in valor) or not any(c.isalnum() for c in valor):
            raise ValueError("La consulta debe contener texto imprimible con letras o números")
        return " ".join(unicodedata.normalize("NFC", valor).split())


class ResultadoManual(Estricto):
    documento: DocumentoId
    titulo_documento: str
    tipo_documento: TipoDocumento
    seccion: str | None
    titulo: str
    cita: str
    pagina: int | None
    texto: Annotated[str, Field(max_length=MAX_TEXTO)]
    texto_truncado: bool
    vigente: bool | None
    revision: str | None
    aplicabilidad: Aplicabilidad
    puntaje: FiniteFloat
    posicion: Annotated[int, Field(ge=1, le=MAX_RESULTADOS)]


class ResultadoManuales(Estricto):
    modo: Literal["textual_postgresql"]
    corpus: Sha256
    resultados: Annotated[list[ResultadoManual], Field(max_length=MAX_RESULTADOS)]


class BuscadorDocumental(Protocol):
    def buscar(self, consulta: ConsultaManuales) -> ResultadoManuales: ...


class BuscadorSemantico(Protocol):
    """Futuro adaptador de citas basado en un espacio operativo validado."""

    @property
    def modelo(self) -> ModeloEmbedding: ...
    def buscar(self, consulta: ConsultaManuales) -> list[str]: ...


_FILAS = """
    c.documento_id, d.metadatos->>'titulo', d.metadatos->>'tipo',
    c.metadatos#>>'{seccion,numero}', c.metadatos->>'titulo', c.id,
    (c.metadatos->>'pagina')::int, left(c.texto, %(max_texto)s), length(c.texto) > %(max_texto)s,
    (c.metadatos->>'vigente')::boolean, d.metadatos->>'revision', c.metadatos->'aplicabilidad'
"""

_FTS_BASE = f"""
WITH consulta AS (
    SELECT coalesce(string_agg(quote_literal(lexema), ' | ' ORDER BY lexema), '')::tsquery AS q
    FROM unnest(tsvector_to_array(to_tsvector('pg_catalog.spanish', %(texto)s))) AS t(lexema)
)
SELECT {_FILAS}, ts_rank_cd(c.texto_busqueda, q, 32)::double precision AS puntaje
FROM rag.chunk c
JOIN rag.documento d ON (d.corpus_id, d.id) = (c.corpus_id, c.documento_id)
CROSS JOIN consulta
WHERE c.corpus_id = %(corpus)s AND c.texto_busqueda @@ q
ORDER BY puntaje DESC, c.id COLLATE "C" LIMIT %(top_k)s
"""

_FTS_OPERATIVA = f"""
WITH codigos_falla AS MATERIALIZED (
    SELECT c.id, dato->>'codigo_fabricante' AS fabricante, dato->>'falla' AS falla
    FROM rag.chunk c
    CROSS JOIN LATERAL jsonb_array_elements(c.metadatos->'codigos_falla') AS dato
    WHERE c.corpus_id = %(corpus)s
      AND (dato->>'codigo_fabricante' = ANY(%(codigos)s) OR dato->>'falla' = ANY(%(codigos)s))
), lexemas AS MATERIALIZED (
    SELECT lexema, quote_literal(lexema)::tsquery AS t
    FROM unnest(tsvector_to_array(to_tsvector(
        'pg_catalog.spanish', %(texto)s || ' ' ||
        coalesce((SELECT string_agg(fabricante || ' ' || falla, ' ') FROM codigos_falla), '')
    ))) AS l(lexema)
), consulta AS (
    SELECT coalesce(string_agg(quote_literal(lexema), ' | ' ORDER BY lexema), '')::tsquery AS q
    FROM lexemas
), pesos AS MATERIALIZED (
    SELECT t, log(1.0 + %(n)s / (1.0 + (
        SELECT count(*) FROM rag.chunk c WHERE c.corpus_id = %(corpus)s AND c.texto_busqueda @@ t
    ))) AS peso
    FROM lexemas
), candidatos AS MATERIALIZED (
    SELECT c.id,
           CASE WHEN lower(c.id) = ANY(%(citas)s) THEN 100.0 ELSE 0 END
           + CASE WHEN lower(c.documento_id) = ANY(%(codigos_min)s) THEN 6.0 ELSE 0 END
           + CASE WHEN c.id IN (SELECT id FROM codigos_falla) THEN 4.0 ELSE 0 END
           + 3.0 * (SELECT count(*) FROM unnest(%(patrones)s::text[]) AS p(patron)
                    WHERE lower(c.texto) ~ patron)
           + COALESCE((SELECT sum(peso * (CASE WHEN c.texto_busqueda @@ t THEN 1.0 ELSE 0 END
                                   + CASE WHEN titulos.v @@ t THEN 0.35 ELSE 0 END))
                       FROM pesos), 0.0)
           + ts_rank_cd(c.texto_busqueda, q, 32) AS puntaje
    FROM rag.chunk c
    JOIN rag.documento d ON (d.corpus_id, d.id) = (c.corpus_id, c.documento_id)
    CROSS JOIN consulta
    CROSS JOIN LATERAL (SELECT to_tsvector('pg_catalog.spanish', c.metadatos->>'titulo') AS v) AS titulos
    WHERE c.corpus_id = %(corpus)s
      AND (c.texto_busqueda @@ q OR lower(c.id) = ANY(%(citas)s)
           OR lower(c.documento_id) = ANY(%(codigos_min)s) OR c.id IN (SELECT id FROM codigos_falla))
      AND c.metadatos#>>'{{aplicabilidad,estado}}' <> 'no_aplicable'
      AND (%(aplicabilidad)s::text IS NULL OR c.metadatos#>>'{{aplicabilidad,estado}}' = %(aplicabilidad)s)
      AND (%(vigencia)s = 'todas'
           OR (%(vigencia)s = 'operativa' AND (d.metadatos->>'vigente')::boolean IS DISTINCT FROM false)
           OR (%(vigencia)s = 'vigente' AND (d.metadatos->>'vigente')::boolean)
           OR (%(vigencia)s = 'obsoleta' AND NOT (d.metadatos->>'vigente')::boolean))
      AND (%(tipo_documento)s::text IS NULL OR d.metadatos->>'tipo' = %(tipo_documento)s)
      AND (%(documento)s::text IS NULL OR c.documento_id = %(documento)s)
      AND (%(fabricante)s::text IS NULL OR lower(d.metadatos->>'fabricante') = lower(%(fabricante)s))
      AND (%(modelo)s::text IS NULL OR c.metadatos->'modelos' ? %(modelo)s)
      AND (%(equipo)s::text IS NULL OR c.metadatos->'equipos' ? %(equipo)s
           OR c.metadatos#>>'{{orden,equipo}}' = %(equipo)s
           OR (d.metadatos->>'tipo' IN ('manual_operadora', 'contrato')
               AND jsonb_array_length(d.metadatos->'equipos') = 0))
    ORDER BY puntaje DESC, c.id COLLATE "C" LIMIT %(candidatos)s
)
SELECT {_FILAS}, ca.puntaje::double precision
FROM candidatos ca
JOIN rag.chunk c ON c.corpus_id = %(corpus)s AND c.id = ca.id
JOIN rag.documento d ON (d.corpus_id, d.id) = (c.corpus_id, c.documento_id)
ORDER BY ca.puntaje DESC, c.id COLLATE "C" LIMIT %(top_k)s
"""

_ESPACIO = """
SELECT 1
FROM rag.indice i JOIN rag.corpus c ON c.id = i.corpus_id
WHERE i.corpus_id = %s AND i.espacio = %s AND i.ambito = 'operativo'
  AND i.dimensiones = %s AND i.modelo = %s
  AND c.chunks = (SELECT count(*) FROM rag.embedding e
                  WHERE e.corpus_id = i.corpus_id AND e.espacio = i.espacio
                    AND e.dimensiones = i.dimensiones)
  AND c.chunks = (SELECT count(*) FROM rag.chunk h WHERE h.corpus_id = i.corpus_id)
"""


def _parametros(consulta: ConsultaManuales, corpus: str, n: int) -> dict:
    texto = consulta.consulta
    codigos = sorted({m.group().upper() for m in _CODIGOS.finditer(texto)})
    citas = {f"{doc.lower()} §{numero.lower()}" for doc, numero in _CITAS.findall(texto)}
    citas.update(f"historial {c.lower()}" for c in codigos if _OT.fullmatch(c.lower()))
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c))
    expansion = " ".join(variantes for patron, variantes in _ALIAS if patron.search(texto))
    return {
        **consulta.filtros.model_dump(), "corpus": corpus, "n": n,
        "texto": f"{texto} {sin_tildes} {expansion}", "codigos": codigos,
        "citas": sorted(citas), "codigos_min": [c.lower() for c in codigos],
        "patrones": [f"(?<![[:alnum:]_-]){c.lower()}(?![[:alnum:]_-])" for c in codigos],
        "candidatos": MAX_CANDIDATOS, "top_k": consulta.top_k, "max_texto": MAX_TEXTO,
    }


class RecuperadorPostgres:
    def __init__(self, config: ConfiguracionPostgres, corpus: str):
        if not _CORPUS_ID.fullmatch(corpus):
            raise ErrorTool("configuracion_invalida", "Se requiere un SHA-256 explícito para el corpus RAG")
        self._config = config
        self.corpus = corpus

    @classmethod
    def desde_entorno(cls) -> "RecuperadorPostgres":
        corpus = os.getenv("PIA3_RAG_CORPUS_ID", "")
        if not _CORPUS_ID.fullmatch(corpus):
            raise ErrorTool("configuracion_invalida", "Configura PIA3_RAG_CORPUS_ID con la identidad del corpus")
        try:
            config = ConfiguracionPostgres.desde_entorno()
        except ValueError:
            raise ErrorTool("configuracion_invalida", "Configuración PostgreSQL incompleta o inválida") from None
        return cls(config, corpus)

    @contextmanager
    def _lectura(self) -> Iterator[psycopg.Connection]:
        with conectar_postgres(self._config, timeout_sentencia_ms=TIMEOUT_SQL_MS) as db:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield db

    def buscar(self, consulta: ConsultaManuales) -> ResultadoManuales:
        return self._buscar(consulta, base=False)

    def buscar_base(self, consulta: ConsultaManuales) -> ResultadoManuales:
        """Variante FTS sin filtros para el comparador fuera del runtime."""
        return self._buscar(consulta, base=True)

    def _buscar(self, consulta, *, base):
        for intento in range(2):
            try:
                with self._lectura() as db:
                    fila = db.execute("SELECT chunks FROM rag.corpus WHERE id = %s", (self.corpus,)).fetchone()
                    if fila is None:
                        raise ErrorTool("corpus_no_disponible", "El corpus indicado no está ingresado en PostgreSQL")
                    parametros = _parametros(consulta, self.corpus, fila[0])
                    if base:
                        parametros["texto"] = consulta.consulta
                    filas = db.execute(_FTS_BASE if base else _FTS_OPERATIVA, parametros).fetchall()
                resultados = [_resultado(fila, pos) for pos, fila in enumerate(filas, 1)]
                return ResultadoManuales(modo="textual_postgresql", corpus=self.corpus, resultados=resultados)
            except (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
                if intento == 1:
                    raise ErrorTool("fuente_no_disponible", "PostgreSQL no pudo completar la lectura") from None
            except (psycopg.Error, ErrorConexionPostgres) as error:
                raise _error_postgres(error) from None

    def verificar_espacio_semantico(self, modelo: ModeloEmbedding) -> None:
        """Valida elegibilidad sin ejecutar búsqueda vectorial ni generar embeddings."""
        disponible = None
        if modelo.ambito == "operativo":
            try:
                with self._lectura() as db:
                    disponible = db.execute(_ESPACIO, (self.corpus, modelo.identidad, modelo.dimensiones,
                                                       Jsonb(modelo.model_dump(mode="json")))).fetchone()
            except (psycopg.Error, ErrorConexionPostgres) as error:
                raise _error_postgres(error) from None
        if not disponible:
            raise ErrorTool("espacio_no_disponible", "Se requiere un espacio operativo, completo y compatible")


def _error_postgres(error: Exception) -> ErrorTool:
    if isinstance(error, psycopg.errors.QueryCanceled):
        return ErrorTool("tiempo_agotado", "La consulta documental superó el plazo de PostgreSQL")
    if isinstance(error, (ErrorConexionPostgres, psycopg.OperationalError)):
        return ErrorTool("fuente_no_disponible", "PostgreSQL no está disponible para la consulta documental")
    return ErrorTool("error_retrieval", "PostgreSQL rechazó la consulta documental")


def _resultado(fila, posicion):
    doc, titulo_doc, tipo, seccion, titulo, cita, pagina, texto, truncado, vigente, revision, aplicable, puntaje = fila
    return ResultadoManual(
        documento=doc, titulo_documento=titulo_doc, tipo_documento=tipo, seccion=seccion, titulo=titulo,
        cita=cita, pagina=pagina, texto=texto, texto_truncado=truncado, vigente=vigente, revision=revision,
        aplicabilidad=Aplicabilidad.model_validate_json(json.dumps(aplicable)), puntaje=puntaje, posicion=posicion,
    )
