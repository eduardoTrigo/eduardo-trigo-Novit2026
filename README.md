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

El agente recibe recorridas por MQTT, transcribe audio en la planta, detecta condiciones de seguridad, procesa partes y clasifica partes confirmados y desvíos de sensores con reglas B1. SQLite conserva las notas, los partes, las decisiones y la outbox. PostgreSQL permite recuperar la memoria conversacional por nota.

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

Con las credenciales definidas, el arranque estándar inicia el agente y PostgreSQL. Si el volumen de PostgreSQL ya existía antes del Issue #17, aplica primero la migración de memoria. [Migración 002](#migración-y-consulta-de-memoria-conversacional)

```bash
docker compose config --quiet
docker compose up -d --build
```

### Proxy del build y del agente

Compose asigna `HTTP_PROXY=http://enlace:3128` y `HTTPS_PROXY=http://enlace:3128` al proceso del agente en `planta-lan`. El hostname `enlace` se resuelve dentro de esa red. El entorno de compilación usa acceso directo o un proxy del host accesible desde el builder. Si aparece `lookup enlace` durante el build, retira de tu `.env` las entradas heredadas del proxy interno y desexporta las variables `HTTP_PROXY` y `HTTPS_PROXY` de la sesión si apuntan a `enlace`. Conserva el proxy de runtime en Compose. El `.env` de Compose sirve para interpolación y configurarlo ahí no configura por sí solo el proxy de Docker. [Docker CLI y proxy](https://docs.docker.com/engine/cli/proxy/) · [Interpolación de Compose](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)

La verificación del Issue #19 cubrió la resolución de Compose y el build del Dockerfile con acceso directo en una etiqueta de prueba. Las configuraciones con proxy corporativo requieren una conexión disponible desde su propio builder.

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

## Tools de consulta de planta

`CatalogoToolsPlanta.catalogo()` devuelve el nombre, la descripción y los esquemas JSON de entrada y salida de cada tool. `ejecutar()` devuelve los resultados estructurados o lanza `ErrorTool` con un código estable. Desde un consumidor asíncrono, usa `asyncio.to_thread` para ejecutar consultas HTTP. [Contratos, fuentes y límites](docs/SPEC_TOOLS_PLANTA.md)

Con el simulador activo, los ejemplos locales consultan equipos y el stock de referencia. Desde el contenedor, usa el destino predeterminado `http://sim:8100`.

```bash
python -c 'from sistema.plant_tools import CatalogoToolsPlanta; c = CatalogoToolsPlanta("http://127.0.0.1:8100"); print(c.ejecutar("consultar_equipos", {"tag": "B-102"}))'
python -c 'from sistema.plant_tools import CatalogoToolsPlanta; print(CatalogoToolsPlanta().ejecutar("consultar_stock", {"codigo": "TX-ROD-CIG", "almacen": "yacimiento"}))'
PIA3_TOOLS_INTEGRATION=1 python -m pytest -q tests/test_plant_tools_integration.py
```

El catálogo se distribuye con el paquete Python y la imagen Docker. Su contenido deriva de los blobs oficiales del commit indicado. Para regenerarlo, usa el script desde un entorno que ya disponga de PyYAML. La ejecución de las tools lee el JSON empaquetado con la biblioteca estándar. [Generador](scripts/actualizar_catalogo_planta.py) · [Recurso y huellas de origen](src/sistema/recursos/catalogo_planta.json)

```bash
python scripts/actualizar_catalogo_planta.py ../academia2026-tp ab65b95e911731351dba395817a74a8cc60da2b2
```

Al actualizar el SHA, revisa el diff del recurso y repite las pruebas de contratos e integración. El stock es el de referencia publicado por Novit.

## Servidor MCP por stdio

El módulo `sistema.mcp_server` expone `consultar_equipos` y `consultar_stock` a un cliente MCP. Se ejecuta como un subproceso local o como un contenedor por sesión con la misma imagen del agente. El cliente conecta stdin y stdout al protocolo. [Configuración local y Docker, contratos y pruebas](docs/SPEC_MCP.md)

```bash
python -m sistema.mcp_server
python -m pytest -q tests/test_mcp_integration.py
```

## PostgreSQL 16 con pgvector

El servicio `postgres` usa la imagen oficial de pgvector `0.8.7-pg16-bookworm`, fijada por digest. Escucha en `postgres:5432` dentro de `planta-lan` y guarda los datos en `pia3_postgres`. El agente y la base tienen límites de 2 CPU/2 GiB y 1 CPU/1 GiB respectivamente. El total de ambos servicios es 3 CPU y 3 GiB. [Imagen oficial de pgvector](https://github.com/pgvector/pgvector#docker) · [Compose](docker-compose.yml)

El rol administrativo (`pia3_admin` por defecto) inicializa la base. El rol de aplicación (`pia3_app`) puede conectarse, usar `vector` y leer, insertar y actualizar las revisiones de memoria. PostgreSQL rechaza contraseñas vacías, contraseñas iguales o roles con el mismo nombre antes de inicializar su volumen.

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

### Migración y consulta de memoria conversacional

En un volumen nuevo, la imagen aplica `001` y `002` en orden. Sobre un volumen que ya tiene `001`, inicia PostgreSQL y envía el archivo idempotente con `psql` como administrador. El comando usa su volumen existente y también funciona si el contenedor todavía carece del montaje de `002`. [Migración 002](infra/postgres/002-memoria.sql)

```bash
docker compose up -d --no-deps --wait postgres
docker compose exec -T postgres sh -ec \
  'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' < infra/postgres/002-memoria.sql
docker compose up -d --build
```

Para consultar una nota, reemplaza `NOTA-00001` por su identificador. La salida contiene datos de planta y se destina a un operador autorizado.

```bash
docker compose exec -T agente-planta python -m sistema.part_memory NOTA-00001
```

`sincronizacion` indica `sincronizada` cuando coinciden las revisiones, `pendiente` cuando PostgreSQL tiene una copia anterior o ninguna y `sin_referencia_local` si solo existe la versión remota. `desalineada` identifica una revisión remota superior a la local y `no_encontrada` indica ausencia en ambas. El agente sigue procesando en SQLite si PostgreSQL falla y retoma los pendientes con espera creciente. El timeout SQL del repositorio es de 3 segundos. La política propone 90 días desde el cierre conocido de una conversación y deja la limpieza automática para una etapa posterior. [Mini spec y política de memoria](docs/SPEC_MEMORIA_CONVERSACIONAL.md)

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
  python -m pytest -q tests/test_postgres_integration.py tests/test_part_memory_integration.py

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

## Ingesta documental de PIA-3

El Issue #23 incorpora la ingesta manual del corpus oficial en el esquema `rag`. Exporta el paquete desde un commit oficial con `scripts/preparar_corpus_rag.py`. El contenedor consume el ZIP por entrada estándar y PostgreSQL conserva documentos, órdenes, metadatos de vigencia y un índice textual en español. Un volumen nuevo aplica `003-rag.sql` con Compose. Un volumen existente requiere aplicar esa migración explícitamente.

Los comandos `ingestar`, `stats` y `verificar` se ejecutan con el rol de aplicación. La indexación semántica queda pendiente de una autorización oficial para un modelo de embeddings. [Procedimiento y pruebas de la ingesta](docs/SPEC_RAG_INDEXACION.md)

## Búsqueda en manuales

`buscar_manuales` consulta la versión del corpus indicada por `PIA3_RAG_CORPUS_ID`. Devuelve texto, citas y metadatos de vigencia y aplicabilidad. Por defecto admite manuales vigentes y órdenes de archivo. Usa `consultar_equipos` para los límites operativos y `consultar_stock` para las cantidades de referencia. [Configuración, filtros y ejemplo](docs/SPEC_RAG_RETRIEVAL.md) · [Evaluación oficial de retrieval](docs/EVALUACION_RAG.md)

## Context Builder

`construir_contexto` recibe la nota y lecturas ya obtenidas. Ordena reglas, documentos, memoria y resultados de tools, conserva la autoridad y procedencia de los datos y aplica un presupuesto configurable. El componente es independiente del arranque del agente. [Contrato y ejemplo Docker de solo lectura](docs/SPEC_CONTEXT_BUILDER.md)
