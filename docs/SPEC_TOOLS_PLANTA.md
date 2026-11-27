# Tools de consulta de planta · Issue #19

Las siete consultas son de solo lectura. `CatalogoToolsPlanta` enumera nombres, descripciones y JSON Schema de entrada y salida. `ejecutar(nombre, argumentos)` acepta un diccionario Python o un objeto JSON y devuelve un diccionario apto para JSON. El catálogo es explícito y puede reutilizarse desde otros adaptadores.

| Tool | Argumentos | Resultado y fuente |
| --- | --- | --- |
| `consultar_equipos` | `tag`, `tipo`, `limite`, todos opcionales | Equipos, aliases, sensores, unidades y límites publicados. `GET /api/planta`. |
| `consultar_historial_sensores` | `tag`, `variable`, `desde`, `hasta`, `limite` opcional | Muestras con fecha y valor, incluido `null` de la fuente. Unidad del sensor de la definición oficial. `GET /api/historian/{tag}/{variable}`. |
| `consultar_historial_ordenes` | `equipo`, `desde`, `hasta`, `limite`, todos opcionales | IDs, fechas, procedencia, tareas y detalle disponible. `GET /api/historial-ordenes`. |
| `consultar_inventario` | `codigo`, `limite`, ambos opcionales | Código, nombre, unidad y costo unitario de referencia. `datos/materiales.yaml`. |
| `consultar_stock` | `codigo`, `almacen` opcional | Cantidades de referencia por almacén, incluido cero. `datos/materiales.yaml`. |
| `consultar_lista_materiales` | `equipo`, `limite` opcional | Repuestos asociados y estado de la lista oficial. `datos/materiales.yaml`. |
| `consultar_catalogo_tareas` | `codigo`, `equipo`, `clase`, `limite`, todos opcionales | Tareas con su aplicabilidad, ejecutor, duración, detención y repuestos publicados. `datos/materiales.yaml`. |

## Validación, límites y errores

Los modelos usan `strict=True` y `extra="forbid"`, también en los objetos anidados. Python requiere objetos `datetime` con zona. JSON acepta fechas ISO 8601 con `T` y `Z` o un offset explícito `±HH:MM`. Se rechazan timestamps numéricos, intervalos invertidos, campos extra, coerciones e identificadores ajenos al catálogo. Los intervalos del historian son inclusivos y se limitan a 45 días. Sus límites se convierten a UTC−03:00 porque ese SHA compara cadenas ISO en la zona fija del simulador. La disponibilidad depende del historial cargado por la planta. Las muestras se ordenan de más antigua a más reciente y las órdenes de más reciente a más antigua.

Las listas incluyen `items`, `total` y `truncado`, con 25 filas por defecto y máximo 100. Para el historian son 60 por defecto y máximo 1.000. La lectura valida hasta 64.800 muestras del historian o 10.000 órdenes. Los argumentos JSON tienen un límite de 16 KiB. El stock devuelve los almacenes publicados o el almacén solicitado. Una lista ausente para un equipo conocido indica `sin_lista`. Los filtros de tareas por equipo devuelven aplicabilidad explícita. Dos tareas con igual código conservan sus equipos y repuestos por separado. Las tareas sin aplicabilidad o repuestos declarados conservan esa ausencia.

`ErrorTool.codigo` distingue `tool_desconocida`, `argumentos_invalidos`, `no_encontrado`, `fuente_no_disponible`, `respuesta_invalida`, `limite_excedido` y `version_no_compatible`. Las series y listas válidas pueden estar vacías. Los registros HTTP se validan antes de aplicar el límite. Las fechas de origen se normalizan explícitamente y los campos opcionales ausentes se omiten. Las órdenes conservan el origen `archivo` o `simulador` cuando la fuente lo indica. Se omiten nombres de solicitantes y técnicos.

Cada llamada HTTP tiene 2 segundos de timeout por operación de red y un plazo total de 5 segundos para la consulta HTTP. Se admite un reintento con 200 ms de espera ante timeout, error transitorio de transporte o HTTP 408, 429, 502, 503 o 504. Las respuestas permanentes o inválidas finalizan en el primer intento. El límite de lectura es de 8 MiB por respuesta. Los destinos admitidos son `http://sim:8100` y el loopback de desarrollo `http://127.0.0.1:8100`, con rutas autorizadas, sin redirecciones ni proxies de entorno. Los catálogos empaquetados se leen y validan localmente, con límite de 256 KiB. Ausencia, corrupción y versión incompatible tienen tratamiento diferenciado y prescinden de reintentos.

## Datos oficiales y alcance

La definición y el catálogo corresponden al simulador `ab65b95e911731351dba395817a74a8cc60da2b2`. Ese SHA publica inventario, stock, materiales y tareas en `datos/materiales.yaml`. El JSON empaquetado conserva esos datos y las identidades y unidades de `datos/planta.yaml`. De `datos/documentos/historial/ordenes.json` solo se extraen los tags válidos para el filtro de órdenes, incluida la clave `PLANTA`. El recurso declara su versión de esquema, el SHA del commit y el SHA-256 de los tres archivos fuente. Las cantidades representan **stock de referencia**. [Contrato oficial](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/docs/contrato.md)

Los flujos operativos conservan los contratos y ventanas de su cliente. El Issue #21 expone equipos y stock mediante MCP. El Issue #25 incorpora `buscar_manuales` como octava tool del catálogo local. [Servidor MCP](SPEC_MCP.md) · [Contrato de búsqueda](SPEC_RAG_RETRIEVAL.md)
