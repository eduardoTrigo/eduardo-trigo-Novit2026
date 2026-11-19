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