# PIA-3 — Sistema de Agentes IA

Trabajo Práctico Integrador — Academia Novit 2026  
**Curso:** Desarrollo de Agentes IA y Software Agéntico  
**Autor:** Eduardo Trigo

## Descripción

Desarrollo de un sistema de agentes IA para asistir en la operación y mantenimiento de la planta simulada de inyección de agua PIA-3.

El sistema permitirá interpretar partes de recorrida por voz, detectar anomalías operativas, emitir alertas de seguridad y consultar información técnica mediante herramientas y recuperación documental (RAG).

## Tecnologías

- Python 3.11+
- LangChain
- Pydantic
- MQTT
- Model Context Protocol (MCP)
- PostgreSQL 16 + pgvector
- Docker Compose
- pytest

## Repositorio oficial

Simulador y contratos de integración:

https://github.com/NovitSoftware/academia2026-tp

## Estructura inicial

```text
src/sistema/    Código principal
tests/          Pruebas automatizadas
docs/           Documentación técnica
evaluacion/     Evaluación y métricas
```

## Instalación local

Crear y activar un entorno virtual en Windows (Git Bash):

```bash
py -3 -m venv .venv
source .venv/Scripts/activate
```

Instalar el proyecto y sus dependencias:

```bash
python -m pip install -e ".[dev]"
```

Comprobar el contrato oficial:

```bash
python -c "from planta_contrato import EstadoSistema; print('Contrato Novit instalado correctamente')"
```

## Estado del desarrollo

El agente recibe recorridas por MQTT, transcribe audio en la planta, detecta condiciones de seguridad, procesa partes y clasifica partes confirmados y desvíos de sensores con reglas B1. SQLite conserva las notas, los partes, las decisiones y la outbox. PostgreSQL está disponible para la futura memoria conversacional y la recuperación documental.

## Arquitectura

El agente y PostgreSQL se ejecutan en la planta. El STT y las decisiones de seguridad son locales. [Arquitectura](docs/ARQUITECTURA.md) · [Plan de entrega](docs/PLAN.md)

## Ejecución con Docker

La solución se ejecuta junto al simulador oficial PIA-3 de Academia Novit 2026.

### 1. Levantar el simulador oficial

Desde la carpeta `academia2026-tp`:

```bash
docker compose up -d
```

### 2. Preparar `.env` y levantar el sistema

Desde la carpeta `Eduardo-Trigo`, crea `.env` si aún no existe.

```bash
test -e .env || cp .env.example .env
```

Completa `POSTGRES_ADMIN_PASSWORD` y `POSTGRES_PASSWORD` en `.env` con dos contraseñas aleatorias distintas generadas por un gestor de contraseñas. Usa comillas simples si contienen `$`. El archivo está excluido de Git. [Plantilla `.env.example`](.env.example)

Con las credenciales definidas, el arranque estándar inicia el agente y PostgreSQL.

```bash
docker compose config --quiet
docker compose up -d --build
```

### 3. Verificar el estado

```bash
docker compose ps
```

### 4. Consultar los logs

```bash
docker compose logs -f agente-planta
```

Para salir de los logs sin detener el contenedor, utilizar `Ctrl + C`.

### 5. Detener nuestros servicios

Desde `Eduardo-Trigo`:

```bash
docker compose stop agente-planta postgres
```

### Servicios del simulador

- Visor 3D: http://localhost:8080
- API Swagger: http://localhost:8100/docs
- Broker MQTT desde Windows: localhost:1883
- Broker MQTT desde Docker: broker:1883

## PostgreSQL 16 con pgvector

El servicio `postgres` usa la imagen oficial de pgvector `0.8.7-pg16-bookworm`, fijada por digest. Escucha en `postgres:5432` dentro de `planta-lan` y guarda los datos en `pia3_postgres`. El agente y la base tienen límites de 2 CPU/2 GiB y 1 CPU/1 GiB respectivamente. El total de ambos servicios es 3 CPU y 3 GiB. [Imagen oficial de pgvector](https://github.com/pgvector/pgvector#docker) · [Compose](docker-compose.yml)

El rol administrativo (`pia3_admin` por defecto) inicializa la base. El rol de aplicación (`pia3_app`) puede conectarse y usar `vector` en la base `pia3`. Los permisos sobre futuras tablas se concederán cuando se creen. PostgreSQL rechaza contraseñas vacías, contraseñas iguales o roles con el mismo nombre antes de inicializar su volumen.

En el primer despliegue, `docker compose pull postgres` descarga la referencia fijada. Para iniciar y verificar solo PostgreSQL con la red de planta disponible, usa estos comandos.

```bash
docker compose up -d --no-deps --wait postgres
docker compose ps postgres
```

El healthcheck autentica al rol de aplicación por TCP y comprueba que `vector` está habilitada. El circuito crítico puede arrancar por separado con `docker compose up -d agente-planta` aunque PostgreSQL esté detenido o sus credenciales aún no estén configuradas.

En un volumen nuevo, la imagen aplica la inicialización en una transacción. Se puede volver a aplicar la misma versión como administrador sobre la base provisionada con los mismos roles. Los cambios en `.env` de un volumen inicializado requieren una operación administrativa de rotación de credenciales. [Inicialización 001](infra/postgres/001-vector.sql)

```bash
docker compose exec -T postgres sh -ec \
  'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/001-vector.sql'
```

Para abrir una conexión desde Python, `ConfiguracionPostgres.desde_entorno()` lee las variables de aplicación exportadas al proceso. `conectar_postgres(config)` abre una sesión con un timeout de conexión de 5 segundos por defecto y expone `ErrorConexionPostgres` si falla. El llamador gestiona la sesión con `with`.

### Pruebas de integración y persistencia aisladas

Con la imagen ya descargada y el entorno Python activo, ejecuta desde Bash. El proyecto recibe un nombre único, crea su propio volumen y publica un puerto aleatorio de prueba en `127.0.0.1`. Las dos contraseñas efímeras se generan dentro del subshell.

```bash
(
  set -e
  project="pia3-pgtest-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])')"
  override="$(mktemp)"
  cat > "$override" <<YAML
services:
  postgres:
    ports:
      - target: 5432
        host_ip: 127.0.0.1
networks:
  planta-lan:
    external: false
    name: $project
YAML
  export POSTGRES_DB=pia3 POSTGRES_ADMIN_USER=pia3_admin POSTGRES_USER=pia3_app
  export POSTGRES_HOST=127.0.0.1 POSTGRES_CONNECT_TIMEOUT=5 PIA3_POSTGRES_INTEGRATION=1
  export POSTGRES_ADMIN_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export POSTGRES_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  pg=(docker compose --env-file .env.example -p "$project" -f docker-compose.yml -f "$override")
  "${pg[@]}" up -d --no-deps --pull never --wait --wait-timeout 75 postgres
  trap '"${pg[@]}" stop postgres' EXIT
  endpoint="$("${pg[@]}" port postgres 5432)"
  export POSTGRES_PORT="${endpoint##*:}"
  python -m pytest -q tests/test_postgres_integration.py

  db=("${pg[@]}" exec -T postgres psql -X -qAt -v ON_ERROR_STOP=1 -U pia3_admin -d pia3)
  "${db[@]}" -c 'CREATE TABLE issue15_probe (id int PRIMARY KEY, muestra vector(3))' \
    -c "INSERT INTO issue15_probe VALUES (15, '[1,2,3]')"
  antes="$("${pg[@]}" ps -q postgres)"
  "${pg[@]}" up -d --no-deps --pull never --wait --wait-timeout 75 --force-recreate postgres
  test "$antes" != "$("${pg[@]}" ps -q postgres)"
  test "$("${db[@]}" -c "SELECT muestra <-> '[1,2,5]'::vector FROM issue15_probe WHERE id = 15")" = 2
)
```

La suite habitual `python -m pytest -q` cubre el cliente con dobles y omite las pruebas que requieren una instancia real. El volumen del ejemplo conserva la fila vectorial después de detener el contenedor.
