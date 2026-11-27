import asyncio
import json
from threading import Event, Lock

import httpx
import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from sistema.mcp_server import crear_servidor
from sistema.plant_tools import CatalogoToolsPlanta


@pytest.mark.asyncio
async def test_publica_solo_dos_tools_con_los_contratos_originales():
    catalogo = CatalogoToolsPlanta()
    contratos = {t["nombre"]: t for t in catalogo.catalogo()}
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        tools = (await cliente.list_tools()).tools
        assert [t.name for t in tools] == ["consultar_equipos", "consultar_stock"]
        for tool in tools:
            original = contratos[tool.name]
            assert tool.description == original["descripcion"]
            assert tool.inputSchema == original["entrada"]
            assert tool.outputSchema == original["salida"]
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.openWorldHint is (tool.name == "consultar_equipos")


@pytest.mark.asyncio
async def test_consulta_equipos_valida_la_salida_y_usa_get_fuera_del_loop():
    peticiones = []

    async def responder(request):
        peticiones.append((request.method, str(request.url)))
        return httpx.Response(200, json={
            "equipos": [{"tag": "B-102", "tipo": "bomba", "descripcion": "Bomba 2", "nombres": ["la dos"]}],
            "sensores": [{"tag": "B-102", "variable": "presion_descarga", "unidad": "kg/cm2"}],
        })

    catalogo = CatalogoToolsPlanta(transport=httpx.MockTransport(responder))
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        resultado = await cliente.call_tool("consultar_equipos", {"tag": "B-102"})
    assert resultado.isError is False
    assert resultado.structuredContent["items"][0]["tag"] == "B-102"
    assert json.loads(resultado.content[0].text) == resultado.structuredContent
    assert peticiones == [("GET", "http://sim:8100/api/planta")]


@pytest.mark.parametrize("nombre,args,codigo", [
    ("consultar_stock", {"codigo": "TX-ROD-CIG", "url": "http://otro"}, "argumentos_invalidos"),
    ("consultar_stock", {"codigo": 1234}, "argumentos_invalidos"),
    ("consultar_stock", {}, "argumentos_invalidos"),
    ("consultar_stock", {"codigo": "TX-INEXISTENTE"}, "no_encontrado"),
    ("consultar_equipos", {"limite": "1"}, "argumentos_invalidos"),
    ("consultar_stock", {"codigo": "X" * (16 * 1024)}, "limite_excedido"),
])
@pytest.mark.asyncio
async def test_errores_de_tool_sin_resultado_exitoso_ni_http(nombre, args, codigo):
    def prohibido(_):
        pytest.fail("Una entrada inválida o local no debe realizar HTTP")

    catalogo = CatalogoToolsPlanta(transport=httpx.MockTransport(prohibido))
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        resultado = await cliente.call_tool(nombre, args)
    assert resultado.isError is True and resultado.structuredContent is None
    assert json.loads(resultado.content[0].text)["codigo"] == codigo


@pytest.mark.parametrize("nombre", ["desconocida", "consultar_inventario"])
@pytest.mark.asyncio
async def test_tool_fuera_de_lista_es_error_de_protocolo(monkeypatch, nombre):
    catalogo = CatalogoToolsPlanta()
    monkeypatch.setattr(catalogo, "ejecutar", lambda *_: pytest.fail("Tool no autorizada llegó al catálogo"))
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        with pytest.raises(McpError) as error:
            await cliente.call_tool(nombre, {})
    assert error.value.error.code == types.INVALID_PARAMS


@pytest.mark.parametrize("fallo,intentos", [(404, 1), (503, 2), (None, 2)])
@pytest.mark.asyncio
async def test_errores_de_fuente_preservan_los_intentos_y_ocultan_detalles(fallo, intentos):
    peticiones = []

    def responder(request):
        peticiones.append(request)
        if fallo is None:
            raise httpx.ReadTimeout("detalle sensible", request=request)
        return httpx.Response(fallo)

    catalogo = CatalogoToolsPlanta(transport=httpx.MockTransport(responder))
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        resultado = await cliente.call_tool("consultar_equipos", {})
    assert resultado.isError and resultado.structuredContent is None
    assert json.loads(resultado.content[0].text)["codigo"] == "fuente_no_disponible"
    assert "detalle sensible" not in resultado.content[0].text
    assert len(peticiones) == intentos


@pytest.mark.asyncio
async def test_salida_invalida_se_informa_como_error_interno_sanitizado(monkeypatch):
    catalogo = CatalogoToolsPlanta()
    monkeypatch.setattr(catalogo, "ejecutar", lambda *_: {"campo": "detalle sensible"})
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        with pytest.raises(McpError) as error:
            await cliente.call_tool("consultar_stock", {"codigo": "TX-ROD-CIG"})
    assert error.value.error.code == types.INTERNAL_ERROR
    assert "detalle sensible" not in error.value.error.message


@pytest.mark.asyncio
async def test_maximo_dos_consultas_simultaneas(monkeypatch):
    catalogo = CatalogoToolsPlanta()
    args = {"codigo": "TX-ROD-CIG", "almacen": "yacimiento"}
    salida = catalogo.ejecutar("consultar_stock", args)
    liberar, lock = Event(), Lock()
    activas = maximo = 0

    def ejecutar(*_):
        nonlocal activas, maximo
        with lock:
            activas += 1
            maximo = max(maximo, activas)
        try:
            assert liberar.wait(5)
            return salida
        finally:
            with lock:
                activas -= 1

    monkeypatch.setattr(catalogo, "ejecutar", ejecutar)
    async with create_connected_server_and_client_session(crear_servidor(catalogo)) as cliente:
        async with asyncio.TaskGroup() as grupo:
            tareas = [grupo.create_task(cliente.call_tool("consultar_stock", args)) for _ in range(5)]
            try:
                async with asyncio.timeout(3):
                    while maximo < 2:
                        await asyncio.sleep(0.01)
                assert await asyncio.wait_for(cliente.send_ping(), timeout=2)
                assert maximo == 2
            finally:
                liberar.set()
    assert maximo == 2 and all(t.result().structuredContent == salida for t in tareas)
