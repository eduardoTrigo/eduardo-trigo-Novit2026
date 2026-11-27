"""Cliente oficial contra procesos stdio reales, en Python y en Docker."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.shared.exceptions import McpError
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS


@asynccontextmanager
async def sesion(config):
    async with asyncio.timeout(25), stdio_client(config) as (leer, escribir):
        async with ClientSession(leer, escribir) as cliente:
            inicio = await cliente.initialize()
            assert inicio.serverInfo.name == "pia3-plant-tools" and inicio.serverInfo.version == "0.1.0"
            assert inicio.protocolVersion in SUPPORTED_PROTOCOL_VERSIONS
            assert inicio.capabilities.tools is not None
            assert [tool.name for tool in (await cliente.list_tools()).tools] == ["consultar_equipos", "consultar_stock"]
            yield cliente


async def comprobar_stock(cliente):
    stock = await cliente.call_tool("consultar_stock", {"codigo": "TX-ROD-CIG", "almacen": "yacimiento"})
    assert stock.isError is False
    assert stock.structuredContent["origen"] == "referencia"
    assert stock.structuredContent["stock"] == {"yacimiento": 0}
    assert json.loads(stock.content[0].text) == stock.structuredContent


@pytest.mark.asyncio
async def test_stdio_real_negocia_descubre_consulta_y_maneja_errores(caplog):
    config = StdioServerParameters(command=sys.executable, args=["-m", "sistema.mcp_server"], env={})
    async with sesion(config) as cliente:
        await comprobar_stock(cliente)
        with pytest.raises(McpError) as error:
            await cliente.call_tool("consultar_inventario", {})
        assert error.value.error.code == types.INVALID_PARAMS
        fallo = await cliente.call_tool("consultar_stock", {})
        assert fallo.isError and fallo.structuredContent is None
    assert not [r for r in caplog.records if r.name.startswith("mcp.client.stdio") and r.levelno >= 30]


def test_proceso_cierra_con_eof_y_stdout_reservado():
    salida = subprocess.run([sys.executable, "-m", "sistema.mcp_server"], input=b"", env=get_default_environment(),
                            capture_output=True, timeout=12, check=True)
    assert salida.stdout == b"" and salida.stderr == b""


def test_destino_invalido_solo_emite_diagnostico_sanitizado_en_stderr():
    env = get_default_environment() | {"PLANTA_API_URL": "http://usuario:valor-sensible@externo"}
    salida = subprocess.run([sys.executable, "-m", "sistema.mcp_server"], input=b"", env=env,
                            capture_output=True, timeout=12)
    assert salida.returncode == 2 and salida.stdout == b""
    assert b"PLANTA_API_URL" in salida.stderr and b"valor-sensible" not in salida.stderr


@pytest.mark.parametrize("con_red", [False, True])
@pytest.mark.skipif(not os.getenv("PIA3_MCP_DOCKER_IMAGE"), reason="Requiere una imagen Docker con MCP")
@pytest.mark.asyncio
async def test_stdio_docker(con_red, caplog):
    imagen = os.environ["PIA3_MCP_DOCKER_IMAGE"]
    red = os.getenv("PIA3_MCP_DOCKER_NETWORK", "planta-lan") if con_red else "none"
    nombre = "pia3-mcp-test-" + uuid4().hex[:12]
    args = ["run", "-i", "--pull", "never", "--read-only", "--name", nombre,
            "--cpus", "0.5", "--memory", "256m", "--network", red]
    if con_red:
        args += ["--env", "PLANTA_API_URL=http://sim:8100"]
    config = StdioServerParameters(command="docker", args=args + [imagen, "python", "-m", "sistema.mcp_server"])
    async with sesion(config) as cliente:
        await comprobar_stock(cliente)
        if con_red:
            equipos = await cliente.call_tool("consultar_equipos", {"tag": "B-102"})
            assert equipos.isError is False and equipos.structuredContent["total"] == 1
            assert equipos.structuredContent["items"][0]["manometro_local"]["unidad"] == "psi"
    formato = "{{.State.Status}} {{.State.ExitCode}} {{.HostConfig.NanoCpus}} {{.HostConfig.Memory}} {{len .Mounts}} {{len .HostConfig.PortBindings}}"
    real = subprocess.check_output(["docker", "inspect", "--format", formato, nombre], text=True, timeout=10)
    assert real.strip() == "exited 0 500000000 268435456 0 0"
    assert not [r for r in caplog.records if r.name.startswith("mcp.client.stdio") and r.levelno >= 30]
