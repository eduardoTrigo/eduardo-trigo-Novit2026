
# PIA-3 - Academia Novit 2026
# Imagen base Python 3.12
FROM python:3.12-slim

# Dependencias del sistema:
# git: instalar el contrato oficial de Novit
# libgomp1: soporte de ejecucion para CTranslate2
# (utilizado por faster-whisper)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Directorio de trabajo
WORKDIR /app

# Configuracion de Python y cache del modelo STT
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/app/modelos \
    PIA3_DB_PATH=/app/data_runtime/seguridad.sqlite3 \
    PIA3_ENABLE_OUTBOX=0

# Crear directorios persistibles
RUN mkdir -p /app/data_runtime /app/modelos

# Copiar configuracion y codigo
COPY pyproject.toml ./
COPY src/ ./src/

# Instalar las dependencias
RUN python -m pip install --no-cache-dir .

# Ejecutar el sistema integrado de seguridad
CMD ["python", "-m", "sistema.security_integrated"]
