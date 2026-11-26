\set ON_ERROR_STOP on
\getenv app_user PIA3_POSTGRES_APP_USER

BEGIN;
CREATE SCHEMA IF NOT EXISTS memoria;
CREATE TABLE IF NOT EXISTS memoria.partes (
    nota_id text PRIMARY KEY,
    revision bigint NOT NULL CHECK (revision > 0),
    documento jsonb NOT NULL,
    sincronizada_en timestamptz NOT NULL DEFAULT statement_timestamp()
);

GRANT USAGE ON SCHEMA memoria TO :"app_user";
GRANT SELECT, INSERT ON memoria.partes TO :"app_user";
GRANT UPDATE (revision, documento, sincronizada_en) ON memoria.partes TO :"app_user";
COMMIT;
