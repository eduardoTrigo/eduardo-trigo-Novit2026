\set ON_ERROR_STOP on
\getenv app_user PIA3_POSTGRES_APP_USER

BEGIN;
CREATE SCHEMA IF NOT EXISTS rag;

CREATE TABLE IF NOT EXISTS rag.corpus (
    id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
    preparacion integer NOT NULL CHECK (preparacion > 0),
    manifiesto jsonb NOT NULL,
    sha_preparado text NOT NULL CHECK (sha_preparado ~ '^[0-9a-f]{64}$'),
    documentos integer NOT NULL CHECK (documentos > 0),
    chunks integer NOT NULL CHECK (chunks > 0),
    ingresado_en timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE IF NOT EXISTS rag.documento (
    corpus_id text NOT NULL REFERENCES rag.corpus (id),
    id text NOT NULL CHECK (id <> ''),
    metadatos jsonb NOT NULL,
    PRIMARY KEY (corpus_id, id)
);

CREATE TABLE IF NOT EXISTS rag.chunk (
    corpus_id text NOT NULL,
    id text NOT NULL CHECK (id <> ''),
    documento_id text NOT NULL,
    metadatos jsonb NOT NULL,
    texto text NOT NULL CHECK (length(btrim(texto)) > 0),
    texto_embedding text NOT NULL CHECK (length(btrim(texto_embedding)) > 0),
    sha_texto text NOT NULL CHECK (sha_texto ~ '^[0-9a-f]{64}$'),
    texto_busqueda tsvector GENERATED ALWAYS AS (to_tsvector('pg_catalog.spanish'::regconfig, texto)) STORED,
    PRIMARY KEY (corpus_id, id),
    FOREIGN KEY (corpus_id, documento_id) REFERENCES rag.documento (corpus_id, id)
);
CREATE INDEX IF NOT EXISTS rag_chunk_texto_gin ON rag.chunk USING gin (texto_busqueda);

CREATE TABLE IF NOT EXISTS rag.indice (
    corpus_id text NOT NULL REFERENCES rag.corpus (id),
    espacio text NOT NULL CHECK (espacio ~ '^[0-9a-f]{64}$'),
    ambito text NOT NULL CHECK (ambito IN ('operativo', 'prueba')),
    dimensiones integer NOT NULL CHECK (dimensiones BETWEEN 1 AND 16000),
    modelo jsonb NOT NULL,
    ingresado_en timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (corpus_id, espacio),
    UNIQUE (corpus_id, espacio, dimensiones),
    CHECK (modelo ->> 'dimensiones' IS NOT DISTINCT FROM dimensiones::text),
    CHECK (modelo ->> 'ambito' IS NOT DISTINCT FROM ambito)
);

CREATE TABLE IF NOT EXISTS rag.embedding (
    corpus_id text NOT NULL,
    espacio text NOT NULL,
    chunk_id text NOT NULL,
    dimensiones integer NOT NULL,
    vector public.vector NOT NULL,
    PRIMARY KEY (corpus_id, espacio, chunk_id),
    FOREIGN KEY (corpus_id, chunk_id) REFERENCES rag.chunk (corpus_id, id),
    FOREIGN KEY (corpus_id, espacio, dimensiones) REFERENCES rag.indice (corpus_id, espacio, dimensiones),
    CHECK (public.vector_dims(vector) = dimensiones AND public.vector_norm(vector) > 0)
);

GRANT USAGE ON SCHEMA rag TO :"app_user";
GRANT SELECT, INSERT ON rag.corpus, rag.documento, rag.chunk, rag.indice, rag.embedding TO :"app_user";
COMMIT;
