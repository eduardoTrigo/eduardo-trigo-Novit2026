# Ingesta e indexación documental de PIA-3

## Alcance

El Issue #23 importa el corpus oficial a PostgreSQL 16 y prepara las estructuras para recuperación textual y vectorial. La ejecución es explícita y queda fuera del arranque del agente. RG-1 sigue parcial hasta que exista un índice semántico operativo y se evalúe la recuperación. TM-1 sigue parcial hasta implementar `buscar_manuales`. [Issue #23](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/23)

## Procedencia y paquete

El exportador lee los bytes de Git del simulador vecino. El commit `ab65b95e911731351dba395817a74a8cc60da2b2` coincide con la referencia del proyecto. `datos/documentos/README.md`, `planta/corpus/doc.py` y `planta/corpus/generar.py` explican la estructura y la formación de las citas. [Árbol oficial del simulador](https://github.com/NovitSoftware/academia2026-tp/tree/ab65b95e911731351dba395817a74a8cc60da2b2)

| Fuente | Bytes | SHA-256 |
| --- | ---: | --- |
| `datos/documentos/indice.json` | 100.412 | `adb92e6a66c82550cfa890b38593b5860eec0ec1ff30251f1892325060727b27` |
| `datos/documentos/fragmentos.jsonl` | 378.736 | `a07975aaafd560c7017384c3590237b0cacb5a62ce002847e7bfafd576fd8a81` |
| `datos/documentos/historial/ordenes.json` | 164.034 | `e4af37bbe1e47d54803bb1ad4a7d7338febdc3139cf36f3483df512dfcc52ffb` |
| `datos/planta.yaml` | 3.746 | `851d9c9c2217d6bfd2562e83c6f4683c10264d200362a30af778c30764071ba3` |

El formato ZIP 1 conserva el índice y los fragmentos publicados. Proyecta los equipos y los metadatos de las órdenes. Su manifiesto contiene las cuatro huellas de origen y las de los cuatro miembros. La lista cerrada de rutas de Git y miembros ZIP excluye archivos de corrección, PDF, Markdown y copias de texto de órdenes. La proyección omite los nombres del solicitante y el técnico. El lector valida huellas, nombres y tamaños sin extraer el ZIP.

Dos exportaciones locales produjeron los mismos 105.171 bytes, con SHA-256 `1e923e8cd1dc89d477f77dc0127f9fee3aed242ee7b4419a49f281f2229bdffe`. La identidad documental depende del manifiesto y la versión de preparación. La compresión del ZIP queda fuera de esa identidad. La revisión del árbol oficial no encontró una licencia de redistribución para estos documentos. El paquete queda en `tmp/`, excluido de Git y del contexto Docker. Usar la copia oficial del curso como origen y transferir el paquete local por stdin. [Exportador](../scripts/preparar_corpus_rag.py)

## Validación y chunking

Pydantic valida tipos y campos. La preparación cruza las secciones y órdenes con los fragmentos, valida unicidad y referencias al catálogo, y conserva numeración, apéndices y texto. Se genera un chunk por sección u orden, con el ID y la cita oficiales. La sección original es el padre del chunk técnico. La entrada de embeddings combina título del documento, cita, título de sección y texto. El texto original y su huella se conservan aparte. Las órdenes se distinguen por tipo, mantienen su identidad, equipo y los campos históricos disponibles. Página, sección, revisión y vigencia quedan ausentes para el historial.

| Documento | Secciones u órdenes |
| --- | ---: |
| TX350-D | 112 |
| TX350-C | 108 |
| FC48-3 | 98 |
| MBP-4 | 109 |
| CT-SVP-2025 | 17 |
| CERT-EJ | 14 |
| Historial | 243 |

Son 6 documentos técnicos, 1 agrupador histórico y 701 chunks. Los textos preparados tienen 69–2.781 caracteres, con mediana de 327 y percentil 95 de 1.019. Un conteo aproximado de palabras y puntuación da mínimo 15, mediana 82, percentil 95 de 245 y máximo 567. Para secciones técnicas, la mediana es 105 y el percentil 95 es 263. Para órdenes, la mediana es 78 y el percentil 95 es 92. El resultado corresponde a una aproximación sin tokenizador. Se conservan las tablas y citas de una sola pieza, con cero solapamiento. Ante el límite de un proveedor futuro, la generación se rechaza y se conserva el índice anterior. Una subdivisión posterior deberá conservar padre y cita y versionar la preparación. [Contratos y preparación](../src/sistema/rag_corpus.py)

## Metadatos, vigencia y alcance

- Cada documento técnico conserva título, código, emisor, revisión, fechas, referencias de reemplazo, archivos y equipos declarados. El catálogo oficial aporta modelos y, para manuales de fabricante con evidencia inequívoca, fabricante. El campo `alcance_equipos` indica si los equipos vienen del documento o de una orden.
- Los documentos vigentes aportan 350 secciones. La revisión C aporta 108 secciones no vigentes y señala la revisión D como reemplazo. La revisión D conserva su relación recíproca. Las 243 órdenes carecen de vigencia documental.
- La aplicabilidad técnica registra cita y evidencia. Las secciones TX350-C y TX350-D §3.3 y MBP-4 §5.3 y §17–§17.3 son ajenas a los equipos de PIA-3 en el contexto que declaran y quedan con equipos de aplicación vacíos. Las comparaciones TX350-C y TX350-D §1.1 y §2.1, FC48-3 §2.1, MBP-4 §1.1 y CT-SVP-2025 §1.1 retienen la parte aplicable. El estado `indeterminada` conserva los casos sin evidencia específica de sección. Las referencias explícitas a TX-250, TX-500, FC-24, FC-96, PIA-1 y PIA-2 se registran como menciones.
- MBP-4 §6.1 aporta 11 correspondencias de su columna «Fabricante» para F01–F10. F10 conserva TX-T08 y FC-T04. Cada entrada lleva la cita original de la tabla y el resto de §6 permanece disponible como texto.

## Embeddings y almacenamiento

El Enunciado exige un modelo de la lista cerrada de Novit. El material del Módulo 2.2 explica la técnica y el ejemplo de clase usa `bge-m3` por Ollama. La revisión del material disponible no acreditó su autorización para embeddings del TP. El cliente HTTP del ejemplo permitiría desacoplar la inferencia, con un proceso adicional que consumiría recursos de planta. Una biblioteca de inferencia directa podría evitar ese proceso si el formato de un modelo autorizado la admite. Una vez confirmada la autorización, corresponde fijar artefacto, revisión, licencia, tokenizador, dimensiones, prefijos de documento y consulta, y medir español, memoria, CPU y latencia en planta. [Contrato de embeddings](../src/sistema/rag_embeddings.py)

El proveedor intercambia lotes de hasta 32 elementos. Declara tokenizer, límite, identidad, revisión y configuración compartidos por documentos y consultas. Cada lote recibe un plazo de hasta 15 s dentro de un presupuesto de 120 s. El adaptador debe respetar el plazo durante inferencia. Se validan conteos, dimensiones, finitud y representabilidad en float32. La norma debe admitir distancia coseno. Una carga admite hasta 3.000.000 de componentes. El CLI importa texto e informa por separado los embeddings operativos y de prueba. El proveedor sintético de tres dimensiones existe solo en los tests.

La migración 003 crea `rag.corpus`, `rag.documento`, `rag.chunk`, `rag.indice` y `rag.embedding`. El esquema usa claves por corpus, espacio y chunk. Un espacio identifica la configuración completa del modelo, incluidas sus dimensiones y el ámbito operativo o de prueba. La base comprueba la dimensión y norma del vector y las referencias. El GIN indexa `tsvector` con configuración `spanish`. Para 701 chunks se empieza con distancia vectorial exacta. HNSW se evaluará cuando una medición justifique su costo. El rol de aplicación tiene `USAGE` del esquema y `SELECT` e `INSERT` en estas tablas. El migrador administrativo conserva su rol separado. [Migración 003](../infra/postgres/003-rag.sql) · [PostgreSQL 16 e índices de texto](https://www.postgresql.org/docs/16/textsearch-indexes.html) · [pgvector 0.8.7](https://github.com/pgvector/pgvector/blob/v0.8.7/README.md)

La escritura de cada versión ocurre en una transacción posterior a la inferencia. Las claves únicas resuelven reintentos y escrituras concurrentes. La misma identidad y preparación se reutilizan. Un SHA o una versión de preparación nuevos crean otra versión documental. Un modelo, revisión o configuración nuevos crean otro espacio vectorial. Si una carga falla, el rollback conserva la versión anterior. Reintentar el mismo comando tras corregir la causa. `verificar` recalcula huellas de procedencia y preparación, comprueba conteos e identidad del modelo y detecta espacios incompletos. [Persistencia y verificación](../src/sistema/rag_store.py)

## Operación

En una instalación nueva, prepara el `.env` con las dos credenciales documentadas e inicia Compose. PostgreSQL aplica 001–003 al crear el volumen. Para un volumen existente con 001 y 002, usa la siguiente orden con el servicio PostgreSQL activo y la credencial administrativa del contenedor. Puedes repetirla sobre el mismo volumen. [Configuración general](../README.md#postgresql-16-con-pgvector)

```bash
docker compose exec -T postgres sh -ec \
  'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -' < infra/postgres/003-rag.sql
```

Desde el repositorio y con el entorno Python del proyecto activo, usa otro nombre de salida para cada exportación. El exportador evita sobrescribir paquetes. Para actualizar, revisa el nuevo SHA oficial y pásalo explícitamente con `--commit`.

```bash
PYTHONPATH=src python scripts/preparar_corpus_rag.py \
  --simulador ../academia2026-tp \
  --commit ab65b95e911731351dba395817a74a8cc60da2b2 \
  --salida tmp/rag-corpus.zip
```

Con el contenedor del agente actualizado desde este código, exporta en el shell las variables de aplicación de `.env`. El comando usa el rol de aplicación y se conecta a PostgreSQL por `planta-lan`. Sustituye el ID por el que devuelve el exportador si usas otro SHA.

```bash
(
  set -e
  set -a
  . ./.env
  set +a
  imagen="$(docker compose images -q agente-planta)"
  test -n "$imagen"
  id=83cfec46e9d62b88fcf0ee2767f794fa0fd64ce7277ad41e1d0ccfd20991d1a2
  rag() {
    docker run --rm --pull never -i --read-only --cpus=0.5 --memory=512m \
      --network planta-lan \
      --env POSTGRES_HOST --env POSTGRES_PORT --env POSTGRES_DB \
      --env POSTGRES_USER --env POSTGRES_PASSWORD --env POSTGRES_CONNECT_TIMEOUT \
      "$imagen" python -m sistema.rag_ingest "$@"
  }
  rag ingestar --corpus - < tmp/rag-corpus.zip
  rag stats --id "$id"
  rag verificar --id "$id"
)
```

Para una ejecución desde PowerShell, la redirección binaria del ZIP se puede hacer con `cmd.exe /d /c "docker run ... < tmp\rag-corpus.zip"`. La CLI local admite `--corpus tmp/rag-corpus.zip` y la misma configuración PostgreSQL. El límite del contenedor temporal suma 0,5 CPU y 512 MiB a los 3 CPU y 3 GiB del stack de planta del alumno. Ejecuta una tarea de ingesta a la vez.

## Verificación aislada

Exporta el paquete oficial y asigna su ruta a `PIA3_RAG_CORPUS`. Esto habilita las pruebas de lectura del corpus. Para las pruebas SQL usa un proyecto Compose y volumen propios, un puerto de PostgreSQL publicado solo en `127.0.0.1` y dos credenciales efímeras. Exporta las variables de aplicación apuntando a ese puerto y activa `PIA3_RAG_INTEGRATION=1` y `PIA3_POSTGRES_INTEGRATION=1`. [Ejemplo de entorno PostgreSQL aislado](../README.md#pruebas-de-integración-y-persistencia-aisladas)

```bash
python -m pytest -q tests/test_rag_ingest.py tests/test_postgres_integration.py tests/test_part_memory_integration.py
```

Para comprobar la migración de un volumen existente, crea otro proyecto aislado con un override `volumes: !override` en el servicio PostgreSQL que incluya el volumen de datos y solo los montajes 001 y 002. Inserta una fila testigo en `memoria.partes`, aplica 003 dos veces con el comando anterior para ese proyecto y verifica la fila. Corre las integraciones apuntando al nuevo puerto. Para comprobar persistencia, recrea solo `postgres` en el proyecto aislado, comprueba la identidad del volumen y ejecuta `verificar` y la consulta vectorial. Conserva los volúmenes de prueba.

El 27 de noviembre de 2026 se ejecutó la suite de 26 pruebas de corpus, PostgreSQL y memoria en cada uno de los dos proyectos aislados. Se comprobó PostgreSQL 16 con pgvector 0.8.7, autenticación, permisos limitados, GIN en español y rollback de una actualización fallida. La ejecución sobre el volumen legado conservó la fila previa de `memoria.partes` al aplicar 003 dos veces. La recreación del contenedor nuevo conservó el volumen, las huellas de 701 chunks y 701 vectores de prueba de tres dimensiones. La distancia euclídea entre `[1,2,3]` y `[1,2,5]` devolvió 2. Se observaron cero embeddings operativos.

La prueba Docker pasó el paquete por stdin sin montajes del host ni acceso al simulador vecino. Se usaron los módulos de este Issue sobre la imagen local de pruebas del Issue #21 con sus dependencias instaladas. En esa corrida, el CLI informó 0,574 s para la carga, 0,093–0,094 s al repetir y 0,110 s para verificar tras recrear el contenedor. PostgreSQL aislado tuvo límite de 0,5 CPU y 512 MiB y registró una muestra de 34,71 MiB después de las pruebas. La carga efímera estuvo limitada a otros 0,5 CPU y 512 MiB. Durante la prueba, los servicios del alumno y los temporales sumaron un máximo asignado de 4 CPU y 4 GiB. La muestra de memoria corresponde a PostgreSQL después de las pruebas, sin medir picos ni inferencia semántica.

La suite habitual con el paquete oficial habilitado terminó con 502 pruebas aprobadas y 12 integraciones opcionales omitidas. También pasaron `docker compose --env-file .env.example config --quiet` y `git diff --check`.
