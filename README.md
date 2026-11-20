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

Configuración inicial en progreso.

La integración MQTT, los agentes IA, la detección de anomalías y el despliegue completo mediante Docker Compose se encuentran pendientes de implementación.

## Arquitectura

La distribución de componentes entre la planta y servicios remotos se documentará conforme avance la implementación, respetando las restricciones de recursos y funcionamiento sin enlace del enunciado oficial.

## Ejecución con Docker

La solución se ejecuta junto al simulador oficial PIA-3 de Academia Novit 2026.

### 1. Levantar el simulador oficial

Desde la carpeta `academia2026-tp`:

```bash
docker compose up -d
```

### 2. Levantar nuestro agente

Desde la carpeta `Eduardo-Trigo`:

```bash
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

### 5. Detener nuestro agente

Desde `Eduardo-Trigo`:

```bash
docker compose down
```

Este comando detiene y elimina los contenedores y redes creados por nuestro Compose, pero no elimina la red externa `planta-lan` ni los servicios del simulador oficial.

### Servicios del simulador

- Visor 3D: http://localhost:8080
- API Swagger: http://localhost:8100/docs
- Broker MQTT desde Windows: localhost:1883
- Broker MQTT desde Docker: broker:1883

### Estado actual de implementación

El cliente MQTT recibe notas de recorrida y publica un heartbeat periódico validado mediante `planta_contrato`.

La interpretación de audios, detección de anomalías, persistencia de eventos y cálculo real del estado del enlace todavía están pendientes.