# Servidor MCP de planta · Issue #21

`python -m sistema.mcp_server` inicia `pia3-plant-tools` versión `0.1.0` con transporte stdio. El SDK oficial Python `mcp==1.30.0` resuelve la inicialización, negociación y ciclo de sesión. Es una versión estable de la línea 1.x mantenida y se verificó con Python 3.12, Pydantic 2.14 y HTTPX 0.28. De MesaYa.Mcp de Novit, Clase 2, se toma la separación entre descubrimiento MCP, ejecución y API de negocio. [SDK 1.30.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.30.0) · [Documentación 1.x](https://py.sdk.modelcontextprotocol.io/v1/)

## Contratos y errores

El servidor registra dos nombres explícitos. `tools/list` reutiliza sus descripciones y JSON Schema de entrada y salida, con anotación de solo lectura. El handler de `tools/call` delega en `CatalogoToolsPlanta`, conserva la validación Pydantic y valida la respuesta contra el esquema publicado. Las consultas se ejecutan fuera del event loop con un máximo de dos trabajos simultáneos. Los límites, timeouts y reintentos HTTP pertenecen al catálogo. [Contratos y límites del Issue #19](SPEC_TOOLS_PLANTA.md)

| Tool | Argumentos de ejemplo | Resultado |
| --- | --- | --- |
| `consultar_equipos` | `{"tag":"B-102"}` | `items`, `total` y `truncado` con el equipo, sus aliases y sensores de `GET /api/planta`. |
| `consultar_stock` | `{"codigo":"TX-ROD-CIG","almacen":"yacimiento"}` | Material y stock de referencia. El resultado incluye `origen: "referencia"` y `stock: {"yacimiento": 0}`. |

Los resultados exitosos contienen `structuredContent` y una representación JSON en `content`. Los errores de argumentos, códigos inexistentes y fuente inaccesible llevan `isError: true`, código y mensaje en `content`. Un nombre fuera de la lista devuelve el error de protocolo `-32602`. Un fallo interno devuelve `-32603` con un mensaje genérico. El SDK procesa la estructura de las solicitudes y reserva stdout para el protocolo. Los diagnósticos se emiten por stderr sin incluir argumentos ni trazas. [Tools y errores MCP](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) · [Transporte stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

## Ejecución

Configura al cliente MCP para lanzar el siguiente comando desde el entorno Python del proyecto. El destino HTTP predeterminado es `http://127.0.0.1:8100`. `PLANTA_API_URL` puede seleccionar ese destino o `http://sim:8100`. Los parámetros de las tools se gobiernan por sus esquemas.

```bash
python -m sistema.mcp_server
```

Con la imagen del agente construida y el simulador activo, configura al cliente con este comando. `-i` mantiene stdin conectado. La sesión usa un contenedor de solo lectura, sin montajes ni publicación de puertos, y recibe solo la variable de destino. Una sesión con este nombre agrega 0,5 CPU y 256 MiB al presupuesto de 3 CPU y 3 GiB del agente y PostgreSQL. El proxy de build se configura en el entorno de compilación. [Proxy de build y ejecución](../README.md#proxy-del-build-y-del-agente)

```bash
docker run --rm -i --name pia3-mcp --pull never --read-only --cpus 0.5 --memory 256m \
  --network planta-lan -e PLANTA_API_URL=http://sim:8100 \
  eduardo-trigo-agente-planta:latest python -m sistema.mcp_server
```

El agente operativo tiene su propio ciclo de vida. El proceso MCP termina al cerrar la sesión stdio. Las consultas de stock solo leen el recurso empaquetado y pueden ejecutarse con `--network none`.

## Pruebas con el cliente del SDK

```bash
python -m pytest -q tests/test_mcp_server.py tests/test_mcp_integration.py
PIA3_MCP_DOCKER_IMAGE=eduardo-trigo-agente-planta:latest python -m pytest -q tests/test_mcp_integration.py
```

Las pruebas con Docker comprueban el stock sin red y la consulta de equipos por `sim:8100`. Para una prueba aislada, `PIA3_MCP_DOCKER_NETWORK` selecciona la red privada donde el simulador mantiene el alias `sim`.
