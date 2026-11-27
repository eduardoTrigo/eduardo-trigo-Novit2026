"""Expone consultas de planta a un cliente MCP conectado por stdio."""

import asyncio
import json
import logging
import os
import sys

from anyio import CapacityLimiter, to_thread
from jsonschema import Draft202012Validator
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError

from sistema.plant_tool_models import ErrorTool
from sistema.plant_tools import CatalogoToolsPlanta


logger = logging.getLogger(__name__)
_PERMITIDAS = ("consultar_equipos", "consultar_stock")


def crear_servidor(catalogo: CatalogoToolsPlanta) -> Server:
    servidor = Server("pia3-plant-tools", version="0.1.0")
    contratos = {tool["nombre"]: tool for tool in catalogo.catalogo()}
    herramientas = [
        types.Tool(
            name=nombre,
            description=contratos[nombre]["descripcion"],
            inputSchema=contratos[nombre]["entrada"],
            outputSchema=contratos[nombre]["salida"],
            annotations=types.ToolAnnotations(readOnlyHint=True, openWorldHint=nombre == "consultar_equipos"),
        )
        for nombre in _PERMITIDAS
    ]
    validadores = {tool.name: Draft202012Validator(tool.outputSchema) for tool in herramientas}
    cupo = CapacityLimiter(2)

    @servidor.list_tools()
    async def listar() -> list[types.Tool]:
        return herramientas

    def ejecutar(nombre: str, argumentos: dict) -> types.CallToolResult:
        try:
            # La entrada JSON conserva el límite de bytes y la validación estricta del catálogo.
            resultado = catalogo.ejecutar(nombre, json.dumps(argumentos, ensure_ascii=False, separators=(",", ":")))
            validadores[nombre].validate(resultado)
        except ErrorTool as error:
            mensaje = json.dumps({"codigo": error.codigo, "mensaje": str(error)}, ensure_ascii=False)
            return types.CallToolResult(content=[types.TextContent(type="text", text=mensaje)], isError=True)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(resultado, ensure_ascii=False))],
            structuredContent=resultado,
            isError=False,
        )

    async def llamar(peticion: types.CallToolRequest) -> types.ServerResult:
        nombre = peticion.params.name
        if nombre not in validadores:
            raise McpError(types.ErrorData(code=types.INVALID_PARAMS, message="Tool MCP no disponible"))
        try:
            resultado = await to_thread.run_sync(ejecutar, nombre, peticion.params.arguments or {}, limiter=cupo)
        except Exception:
            logger.error("Fallo interno al ejecutar una consulta MCP")
            raise McpError(types.ErrorData(code=types.INTERNAL_ERROR, message="Fallo interno de consulta")) from None
        return types.ServerResult(resultado)

    # El decorador call_tool del SDK convierte excepciones en isError. El handler tipado
    # permite que una tool no expuesta conserve el error de protocolo del SDK.
    servidor.request_handlers[types.CallToolRequest] = llamar
    return servidor


async def _servir(servidor: Server) -> None:
    async with stdio_server() as (leer, escribir):
        await servidor.run(leer, escribir, servidor.create_initialization_options())


def main() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    # El SDK puede incluir datos de tramas inválidas en sus diagnósticos.
    logging.getLogger("mcp").setLevel(logging.CRITICAL + 1)
    try:
        catalogo = CatalogoToolsPlanta(os.getenv("PLANTA_API_URL", "http://127.0.0.1:8100"))
    except ValueError:
        logger.error("PLANTA_API_URL debe indicar un destino de planta autorizado")
        return 2
    try:
        asyncio.run(_servir(crear_servidor(catalogo)))
    except KeyboardInterrupt:
        pass
    except Exception:
        logger.error("La sesión MCP terminó por un fallo interno")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
