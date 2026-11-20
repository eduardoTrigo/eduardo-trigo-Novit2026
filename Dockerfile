
# Imagen base con Python 3.12
FROM python:3.12-slim

# Git es necesario para instalar el contrato oficial
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Directorio de trabajo dentro del contenedor
WORKDIR /app

# Copiar configuración y código fuente
COPY pyproject.toml ./
COPY src/ ./src/

# Instalar dependencias del proyecto
RUN python -m pip install --no-cache-dir .

# Mostrar logs sin almacenamiento intermedio
ENV PYTHONUNBUFFERED=1

# Iniciar nuestro servicio
CMD ["python", "-m", "sistema.main"]
