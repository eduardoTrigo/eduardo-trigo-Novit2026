import traceback
from unittest.mock import patch

import psycopg
import pytest

from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres, conectar_postgres


def test_carga_configuracion_de_aplicacion_sin_exponer_clave(monkeypatch):
    for variable in ("HOST", "PORT", "DB", "USER", "CONNECT_TIMEOUT"):
        monkeypatch.delenv(f"POSTGRES_{variable}", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "clave-de-prueba")
    monkeypatch.setenv("POSTGRES_ADMIN_PASSWORD", "clave-admin-de-prueba")

    config = ConfiguracionPostgres.desde_entorno()

    assert (config.host, config.puerto, config.base, config.usuario, config.timeout) == (
        "postgres", 5432, "pia3", "pia3_app", 5,
    )
    assert config.clave == "clave-de-prueba"
    assert "clave-de-prueba" not in repr(config)


def test_requiere_clave_de_aplicacion(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setenv("POSTGRES_ADMIN_PASSWORD", "clave-admin-de-prueba")

    with pytest.raises(ValueError, match="POSTGRES_PASSWORD"):
        ConfiguracionPostgres.desde_entorno()


@pytest.mark.parametrize(
    ("variable", "valor"),
    [("POSTGRES_PORT", "65536"), ("POSTGRES_CONNECT_TIMEOUT", "1"),
     ("POSTGRES_CONNECT_TIMEOUT", "abc"), ("POSTGRES_HOST", "")],
)
def test_rechaza_configuracion_invalida(monkeypatch, variable, valor):
    monkeypatch.setenv("POSTGRES_PASSWORD", "clave-de-prueba")
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValueError, match=variable):
        ConfiguracionPostgres.desde_entorno()


def test_conexion_transmite_configuracion_y_timeout(monkeypatch):
    for variable, valor in {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "15432",
        "POSTGRES_DB": "pruebas",
        "POSTGRES_USER": "rol_pruebas",
        "POSTGRES_PASSWORD": "clave = 'prueba'",
        "POSTGRES_CONNECT_TIMEOUT": "3",
    }.items():
        monkeypatch.setenv(variable, valor)

    with patch("sistema.postgres.psycopg.connect") as conectar:
        conexion = conectar_postgres(ConfiguracionPostgres.desde_entorno())

    conectar.assert_called_once_with(
        host="127.0.0.1", port=15432, dbname="pruebas", user="rol_pruebas",
        password="clave = 'prueba'", connect_timeout=3,
    )
    assert conexion is conectar.return_value


def test_traduce_error_de_conexion_sin_exponer_detalles_del_driver():
    config = ConfiguracionPostgres("postgres", 5432, "pia3", "pia3_app", "clave-de-prueba", 5)
    error = psycopg.OperationalError("falló la conexión con clave-de-prueba")

    with patch("sistema.postgres.psycopg.connect", side_effect=error):
        with pytest.raises(ErrorConexionPostgres, match="PostgreSQL") as fallo:
            conectar_postgres(config)

    assert "clave-de-prueba" not in "".join(traceback.format_exception(fallo.value))
