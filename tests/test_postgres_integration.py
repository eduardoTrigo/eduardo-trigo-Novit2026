import os
from dataclasses import replace

import pytest

from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres, conectar_postgres


pytestmark = pytest.mark.skipif(
    os.getenv("PIA3_POSTGRES_INTEGRATION") != "1",
    reason="Requiere una instancia PostgreSQL aislada y PIA3_POSTGRES_INTEGRATION=1",
)


@pytest.fixture
def configuracion():
    return ConfiguracionPostgres.desde_entorno()


def test_servidor_postgres_16_y_operacion_vectorial_real(configuracion):
    with conectar_postgres(configuracion) as conexion:
        assert conexion.info.server_version // 10000 == 16
        assert conexion.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone() == ("0.8.7",)
        assert conexion.execute(
            "SELECT '[1,2,3]'::vector <-> '[1,2,5]'::vector"
        ).fetchone() == pytest.approx((2.0,))


def test_aplicacion_puede_conectar_con_privilegios_limitados(configuracion):
    with conectar_postgres(configuracion) as conexion:
        assert conexion.execute(
            """
            SELECT current_user, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                   rolbypassrls, has_schema_privilege('public', 'CREATE')
              FROM pg_roles WHERE rolname = current_user
            """
        ).fetchone() == (configuracion.usuario, False, False, False, False, False, False)


def test_postgres_rechaza_clave_incorrecta(configuracion):
    config_invalida = replace(configuracion, clave=configuracion.clave + "-incorrecta")

    with pytest.raises(ErrorConexionPostgres, match="PostgreSQL"):
        conectar_postgres(config_invalida)
