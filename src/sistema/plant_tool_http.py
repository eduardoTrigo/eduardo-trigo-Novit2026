"""Lectura HTTP acotada y exclusiva de los endpoints de consulta del Issue #19."""

import asyncio
import json
import re

import httpx

from sistema.plant_tool_models import ErrorTool


DESTINOS = frozenset({"http://sim:8100", "http://127.0.0.1:8100"})
MAX_BYTES_HTTP = 8 * 1024 * 1024
TIMEOUT_HTTP = 2.0
PLAZO_HTTP = 5.0
ESPERA_REINTENTO = 0.2
_TRANSITORIOS = {408, 429, 502, 503, 504}
_HISTORIAN = re.compile(r"/api/historian/[A-Z0-9-]+/[a-z0-9_]+")


class LectorPlanta:
    def __init__(self, destino: str, transport: httpx.AsyncBaseTransport | None = None):
        if destino not in DESTINOS:
            raise ValueError("Destino de tools no autorizado")
        self.destino = destino
        self.transport = transport

    def __call__(self, url: str, params: dict | None):
        ruta = url.removeprefix(self.destino)
        if not url.startswith(self.destino) or (
            ruta not in {"/api/planta", "/api/historial-ordenes"} and _HISTORIAN.fullmatch(ruta) is None
        ):
            raise ErrorTool("argumentos_invalidos", "Ruta de consulta no autorizada")
        return asyncio.run(self._leer(url, params))

    async def _leer(self, url: str, params: dict | None):
        try:
            async with asyncio.timeout(PLAZO_HTTP), httpx.AsyncClient(
                timeout=TIMEOUT_HTTP, transport=self.transport, trust_env=False, follow_redirects=False,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as cliente:
                for intento in range(2):
                    try:
                        async with cliente.stream("GET", url, params=params) as respuesta:
                            estado = respuesta.status_code
                            if estado == 200:
                                return await _leer_json(respuesta)
                            if estado not in _TRANSITORIOS or intento == 1:
                                raise ErrorTool("fuente_no_disponible", f"La fuente respondió HTTP {estado}")
                    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                        if intento == 1:
                            raise ErrorTool("fuente_no_disponible", "No se pudo consultar la planta") from None
                    await asyncio.sleep(ESPERA_REINTENTO)
        except TimeoutError:
            raise ErrorTool("fuente_no_disponible", "Plazo de consulta agotado") from None
        except httpx.HTTPError:
            raise ErrorTool("fuente_no_disponible", "El transporte de la planta falló") from None


async def _leer_json(respuesta: httpx.Response):
    mime = respuesta.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime != "application/json" or respuesta.headers.get("content-encoding", "identity").lower() != "identity":
        raise ErrorTool("respuesta_invalida", "La respuesta debe contener JSON sin compresión")
    longitud = respuesta.headers.get("content-length")
    if longitud is not None:
        try:
            bytes_declarados = int(longitud)
            if bytes_declarados < 0:
                raise ValueError
        except ValueError:
            raise ErrorTool("respuesta_invalida", "Longitud de respuesta inválida") from None
        if bytes_declarados > MAX_BYTES_HTTP:
            raise ErrorTool("limite_excedido", "La respuesta supera el límite de tamaño")
    contenido = bytearray()
    async for bloque in respuesta.aiter_bytes():
        if len(contenido) + len(bloque) > MAX_BYTES_HTTP:
            raise ErrorTool("limite_excedido", "La respuesta supera el límite de tamaño")
        contenido.extend(bloque)
    try:
        return json.loads(contenido)
    except (UnicodeError, ValueError, RecursionError):
        raise ErrorTool("respuesta_invalida", "La fuente devolvió JSON inválido") from None
