import os
from dataclasses import dataclass, field

import psycopg


@dataclass(frozen=True)
class ConfiguracionPostgres:
    host: str
    puerto: int
    base: str
    usuario: str
    clave: str = field(repr=False)
    timeout: int = 5

    @classmethod
    def desde_entorno(cls) -> "ConfiguracionPostgres":
        host = os.getenv("POSTGRES_HOST", "postgres")
        base = os.getenv("POSTGRES_DB", "pia3")
        usuario = os.getenv("POSTGRES_USER", "pia3_app")
        clave = os.getenv("POSTGRES_PASSWORD")
        if not clave:
            raise ValueError("POSTGRES_PASSWORD debe estar definida")
        for variable, valor in (("HOST", host), ("DB", base), ("USER", usuario)):
            if not valor.strip():
                raise ValueError(f"POSTGRES_{variable} debe estar definida")

        puerto = _entero_de_entorno("POSTGRES_PORT", 5432)
        if not 1 <= puerto <= 65535:
            raise ValueError("POSTGRES_PORT debe estar entre 1 y 65535")
        timeout = _entero_de_entorno("POSTGRES_CONNECT_TIMEOUT", 5)
        if timeout < 2:
            raise ValueError("POSTGRES_CONNECT_TIMEOUT debe ser de al menos 2 segundos")
        return cls(host, puerto, base, usuario, clave, timeout)


def _entero_de_entorno(variable: str, defecto: int) -> int:
    try:
        return int(os.getenv(variable, str(defecto)))
    except ValueError:
        raise ValueError(f"{variable} debe ser un entero") from None


class ErrorConexionPostgres(RuntimeError):
    """No se pudo abrir una conexión con PostgreSQL."""


def conectar_postgres(config: ConfiguracionPostgres) -> psycopg.Connection:
    """Abre una conexión. El llamador la cierra al salir de su bloque with."""
    try:
        return psycopg.connect(
            host=config.host,
            port=config.puerto,
            dbname=config.base,
            user=config.usuario,
            password=config.clave,
            connect_timeout=config.timeout,
        )
    except psycopg.Error:
        raise ErrorConexionPostgres("No se pudo conectar a PostgreSQL") from None
