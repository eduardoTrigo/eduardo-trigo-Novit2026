# Recuperación documental y buscar_manuales

El Issue #25 agrega un recuperador léxico de solo lectura y la octava tool de `CatalogoToolsPlanta`. Cada consulta apunta al SHA-256 de un corpus ingresado. La tool obtiene la configuración PostgreSQL cuando se invoca. Los consumidores también pueden inyectar un `BuscadorDocumental` por el constructor. MCP publica `consultar_equipos` y `consultar_stock` mediante su allowlist. [Issue #25](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/25)

## Consulta, ranking y citas

PostgreSQL 16 ejecuta Full Text Search con el diccionario `spanish` y el `tsvector` de `rag.chunk` que soporta el GIN existente. La consulta se normaliza a NFC. PostgreSQL convierte el texto en lexemas y construye una unión OR a partir de esos lexemas escapados. El comparador FTS base puntúa con `ts_rank_cd(..., 32)` sobre los 701 chunks completos. BM25 oficial es otra variante léxica ejecutada por el evaluador Novit. Utiliza su tokenización, IDF y saturación por frecuencia y longitud. [PostgreSQL 16 y búsqueda de texto](https://www.postgresql.org/docs/16/textsearch-controls.html) · [Evaluación reproducible](EVALUACION_RAG.md)

La variante operativa combina cobertura de términos ponderada por frecuencia documental, coincidencia en títulos y `ts_rank_cd`. Agrega variantes sin tildes y un mapa acotado de abreviaturas técnicas, como `empaq` y `ΔP`. Prioriza citas exactas, IDs de órdenes, documentos, TAG y códigos que aparecen como unidades completas. Las correspondencias de fabricante y F01–F10 se leen de los metadatos persistidos de MBP-4 §6.1. Una consulta por `TX-T07` puede recuperar esa tabla y secciones vinculadas con `F09`. La recuperación conserva el texto y la cita publicados. [Corpus oficial](https://github.com/NovitSoftware/academia2026-tp/tree/ab65b95e911731351dba395817a74a8cc60da2b2/datos/documentos)

El ranking se ordena por puntaje descendente y por cita con colación `C` en empates. El filtro opera antes del recorte. Se conservan hasta 100 candidatos ordenados para recuperar los datos de salida, con un máximo de 20 resultados. La clave `(corpus_id, id)` de `rag.chunk` y el ID oficial usado como cita garantizan unicidad.

## Contrato de la tool

`consulta` admite de 1 a 400 caracteres con letras o números. `top_k` admite enteros de 1 a 20 y vale 5 por defecto. Ambos objetos, argumentos y filtros, usan Pydantic estricto y rechazan campos extra. Los filtros activos se combinan con AND.

| Filtro | Comportamiento |
| --- | --- |
| `equipo` | TAG documental o de orden histórica. Incluye manuales de operadora y contratos de alcance general que carecen de TAG declarados. |
| `fabricante`, `modelo` | Fabricante documental sin distinción de mayúsculas y modelo de aplicación por código. |
| `tipo_documento`, `documento` | `manual_fabricante`, `manual_operadora`, `contrato` o `historial`. Admite ID exacto de documento. |
| `vigencia` | `operativa` incluye revisiones vigentes y órdenes. `vigente` incluye solo documentos vigentes. `obsoleta` recupera revisiones reemplazadas. `todas` permite ambas revisiones y el historial. |
| `aplicabilidad` | Permite exigir `confirmada` o `indeterminada`. Con cualquier filtro de vigencia se descartan secciones documentadas como `no_aplicable` a PIA-3. |

El historial devuelve la cita `historial OT-…`, el tipo `historial` y valores nulos en página, sección, revisión y vigencia. Las menciones comparativas conservan su aplicabilidad declarada. Las celdas indeterminadas mantienen esa marca y no adquieren confirmación por mencionar un equipo.

La salida contiene `modo: textual_postgresql`, `corpus` y `resultados`. Cada resultado contiene documento, tipo y título, sección, cita, página, texto, revisión, vigencia, aplicabilidad con evidencia cuando existe, puntaje y posición. Se devuelven hasta 3.000 caracteres de texto por resultado y `texto_truncado` indica el recorte. Una búsqueda válida sin coincidencias devuelve una lista vacía. Los límites operativos se consultan en `consultar_equipos`. Las cantidades de referencia se consultan en `consultar_stock`.

## Operación y fallos

Configura `PIA3_RAG_CORPUS_ID` con el SHA-256 devuelto por la ingesta en el entorno que invoca la tool. Usa las credenciales PostgreSQL del rol de aplicación. En el servicio Compose actualizado, una consulta puntual puede indicar la identidad con `exec -e`.

```bash
docker compose exec -T \
  -e PIA3_RAG_CORPUS_ID=83cfec46e9d62b88fcf0ee2767f794fa0fd64ce7277ad41e1d0ccfd20991d1a2 \
  agente-planta python -c \
  'import json; from sistema.plant_tools import CatalogoToolsPlanta; print(json.dumps(CatalogoToolsPlanta().ejecutar("buscar_manuales", {"consulta": "TX-T07", "top_k": 1, "filtros": {"documento": "MBP-4"}}), ensure_ascii=False))'
```

La consulta abre una conexión mediante `ConfiguracionPostgres` y `conectar_postgres`. PostgreSQL ejecuta una transacción `REPEATABLE READ READ ONLY` con `statement_timeout` de 2,5 s y parámetros enlazados. Un error de serialización o interbloqueo admite un reintento. Para un timeout de sentencia, una conexión inaccesible o un error SQL permanente, la consulta termina con un error controlado. [Transacciones de solo lectura en PostgreSQL 16](https://www.postgresql.org/docs/16/sql-set-transaction.html)

| Código | Causa |
| --- | --- |
| `argumentos_invalidos` | Entrada incompatible con el esquema. |
| `configuracion_invalida` | Identidad de corpus o configuración PostgreSQL inválida. |
| `corpus_no_disponible` | Corpus seleccionado ausente de PostgreSQL. |
| `fuente_no_disponible` | Conexión fallida o error transitorio agotado. |
| `tiempo_agotado` | PostgreSQL canceló la sentencia por plazo. |
| `error_retrieval` | PostgreSQL rechazó la consulta. |

## Estado semántico

El ejemplo del Módulo 2.2 usa `bge-m3`. La búsqueda en el Enunciado, el Anexo, el módulo y los materiales del curso disponibles no acreditó su autorización para embeddings del TP. El contrato `BuscadorSemantico` y la verificación de espacio operativo permiten comprobar identidad, configuración, dimensiones y cobertura antes de incorporar un adaptador. El SQL textual consulta los documentos y el espacio semántico queda sin uso en la tool. Siguen pendientes el proveedor autorizado, los embeddings reales, la búsqueda por coseno y RRF. [Mini spec de la ingesta](SPEC_RAG_INDEXACION.md) · [Resultados de evaluación](EVALUACION_RAG.md)
