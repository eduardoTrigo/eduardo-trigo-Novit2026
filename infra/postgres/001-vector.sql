\set ON_ERROR_STOP on
\getenv app_user PIA3_POSTGRES_APP_USER
\getenv app_password PIA3_POSTGRES_APP_PASSWORD

BEGIN;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- El rol existente conserva su contraseña al volver a aplicar este archivo.
SET LOCAL log_statement = 'none';
SET LOCAL log_min_error_statement = 'panic';
SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'app_user', :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
COMMIT;
