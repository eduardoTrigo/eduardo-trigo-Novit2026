# Context Builder de PIA-3

El Anexo de Entrega 1, página 4, pide diseñar el Context Builder y sitúa el análisis completo en Entrega 2. El Issue #27 implementa un ensamblador determinista para futuros consumidores. Su entrada contiene lecturas ya obtenidas. El ejemplo de integración obtiene datos de forma explícita y el agente habitual sigue usando sus propios circuitos. [Issue #27](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/27)

## Contratos y orden

`construir_contexto(EntradaContexto)` recibe nota, tarea, fecha de evaluación, alcance, resultados de fuentes, lecturas de memoria y presupuesto. `NotaContexto.desde_recorrida` conserva el ID, el timestamp original con offset y el texto del turno. Los modelos son estrictos y rechazan campos extra. La entrada reutiliza resultados del catálogo, resultados RAG y, para las filas conversacionales, `FilaParte` del contrato. [Contratos Python](../src/sistema/context_builder_models.py) · [Validación estricta de Pydantic](https://docs.pydantic.dev/latest/concepts/strict_mode/)

`ResultadoContexto` contiene el objeto `ContextoModelo`, las decisiones agregadas por fuente y estado, y el consumo. La serialización conserva este orden de bloques.

1. Instrucciones confiables definidas en código.
2. Contratos pertinentes con nombres de parámetros y campos requeridos, derivados del catálogo.
3. Documentos ordenados por posición de recuperación y cita.
4. Memoria de la nota y estado de revisión.
5. Páginas de origen, resultados de tools, contradicciones y estado de las fuentes.
6. Entrada original.

El plan selecciona por tarea y alcance. `parte` y `aclaracion` admiten el catálogo de los TAG solicitados y el historian de las variables indicadas. `documentacion` admite `buscar_manuales` y puede incorporar equipos, mediciones y órdenes si el alcance lo pide. `materiales` admite fuentes de referencia según los códigos o equipos solicitados. El consumidor autentica y autoriza al solicitante antes de obtener datos. El plan limita qué resultados puede incorporar al contexto y el ensamblador carece de conexiones o ejecución de tools. [Ensamblador](../src/sistema/context_builder.py) · [Catálogo](SPEC_TOOLS_PLANTA.md)

## Autoridad, procedencia y frescura

| Necesidad | Fuente y selección |
| --- | --- |
| TAG, nombres, unidades y límites | `consultar_equipos` aporta la definición oficial para los TAG y variables solicitados. Se registra cuándo se obtuvo. |
| Mediciones | El historian aporta valor, timestamp y unidad. La ventana termina en el timestamp de la nota y admite hasta 600 s de antigüedad por defecto. Las muestras nulas conservan su valor ausente. |
| Inventario, materiales y stock | Las tools de referencia conservan origen y SHA. Las cantidades, incluido cero, se rotulan como referencia. El catálogo actual carece de fuente de stock dinámico. |
| Conocimiento técnico y procedimientos | `buscar_manuales` aporta corpus, modo, cita, página, sección, revisión y aplicabilidad. El ensamblador excluye registros obsoletos y secciones no aplicables y conserva la marca indeterminada. |
| Órdenes | Se incluyen con alcance explícito y se etiquetan como antecedentes. Un resultado documental de tipo historial conserva su tipo y cita de orden. |
| Estado del parte | SQLite conserva la referencia de revisión. Solo se usa una captura remota como vigente cuando coincide con esa referencia. |

Una contradicción entre límites se registra cuando el llamador suministra un `LimiteDocumental` estructurado, con una cita elegible y el mismo TAG, variable y tipo de límite que la definición oficial. Se conservan ambos valores, sus unidades y el corpus. Ante unidades distintas el resultado es indeterminado. La autoridad operativa corresponde a la definición oficial. El ensamblador carece de un extractor de valores desde prosa. Una lectura posterior a la fecha de evaluación se marca como indeterminada.

### Cobertura de las consultas paginadas

`paginas_fuente` registra una entrada por lectura pertinente de equipos, historial de sensores, historial de órdenes, inventario, lista de materiales o catálogo de tareas. Cada entrada conserva su identidad local, la fuente, la fecha de lectura, el `total` informado, el número de filas `recibidos` y el `truncado` original. Los resultados enlazan esas entradas por ID en `paginas`. Las fuentes de referencia conservan origen y SHA, y la lista de materiales conserva el equipo consultado. La proyección solo puede conservar los filtros que la respuesta de la tool expone.

`total` corresponde al recuento que informa la tool para esa consulta antes del límite de la página. En el historian se conserva el TAG, la variable, la unidad, el intervalo solicitado y los extremos de las muestras recibidas antes del filtrado del Context Builder. La última muestra recibida es un extremo observado. Como la tool limita una serie ordenada de más antigua a más reciente, una página truncada puede omitir la cola, incluidas las muestras más recientes.

Una serie agrupada enlaza todas sus páginas originales. Los totales de consultas superpuestas se conservan por separado. La presencia de una página íntegra deja intacta la advertencia de las demás páginas truncadas. Una página sin filas puede carecer de extremos temporales. Los metadatos sensibles se omiten y la advertencia de truncamiento se conserva.

`estado_fuentes` distingue `truncada_origen` de `fuera_de_alcance`, `obsoleta`, `duplicado`, `compactado` y `omitido_presupuesto`. `sin_coincidencias` se refiere a las filas recibidas. Si además aparece `truncada_origen`, la fuente puede tener coincidencias en las filas que quedaron fuera. `vacia` indica que se recibió una página íntegra sin filas.

## Memoria y degradación

`proyectar_memoria` acepta `CapturaMemoria` del Issue #17 y valida la identidad interna, los vínculos entre borradores y respuestas y los tipos oficiales de nota, filas y mensajes. Proyecta los IDs, versiones, filas, preguntas, respuestas, resultados y cierre disponibles. Omite nombres, URLs, payloads completos y contexto libre preexistente. La misma nota y el mismo instante original son requisitos de selección. [Memoria conversacional](SPEC_MEMORIA_CONVERSACIONAL.md)

Una captura local de la revisión SQLite indicada prevalece. Si falta esa captura, una proyección PostgreSQL sincronizada puede aportar los turnos. Una revisión remota atrasada, adelantada o sin referencia local queda identificada como `pendiente`, `desalineada` o `sin_referencia_local` y su contenido queda fuera. La bitácora registra fallos de fuentes por códigos controlados. Un resultado vacío, una fuente no suministrada, una muestra antigua y una exclusión por presupuesto tienen estados distintos. El consumidor conserva los timeouts y reintentos de las fuentes existentes. El ensamblado agrega cero reintentos.

## Presupuesto y compactación

El límite global por defecto es de 4.200 tokens aproximados y 20.000 bytes UTF-8 del JSON que recibe el futuro consumidor. Se reserva espacio para 900 tokens de salida. Los topes por componente son 1.400 para tools, 1.800 para documentos y 1.600 para la memoria proyectada. Con una ventana efectiva declarada, la entrada se limita además al 75 % de la ventana, descontando la reserva. El criterio sigue el umbral de 70–80 % del Módulo 2.1. Los valores por defecto son límites de este incremento y la ventana autorizada sigue por confirmar.

Se estima el máximo entre los bytes divididos por cuatro, redondeados hacia arriba, y el conteo de palabras y signos. El resultado identifica el cálculo como aproximado. El parámetro `contar_tokens` permite medir el mismo JSON con un tokenizer compatible cuando se confirme el modelo. El informe de decisiones viaja fuera del JSON medido.

Se filtran elementos fuera del alcance y se deduplican muestras, citas y turnos. Para series extensas se conserva una muestra inicial y hasta las quince recibidas más recientes dentro del alcance. Bajo presión adicional se conservan los extremos antes de omitir un bloque. Las páginas de origen y sus advertencias sobreviven al recorte de filas. Cuentan en el presupuesto global de tokens y bytes. Luego se reducen los documentos de menor rango y se compactan los turnos antiguos. Se mantienen los IDs, el borrador actual, las aclaraciones ligadas a decisiones y las filas con valores distintos. Si se repiten filas, se puede conservar una referencia al borrador que las contiene. Si reglas, entrada original, decisiones protegidas y estados necesarios superan el presupuesto, se devuelve `presupuesto_insuficiente`.

## Separación y datos sensibles

Solo el código del módulo produce instrucciones confiables. Los textos externos se conservan como valores de cadenas JSON. Un documento con delimitadores o frases que pretendan cambiar de rol sigue dentro del bloque de documentos. Los contratos permitidos provienen del registro tipado de herramientas. El ensamblador carece de ejecución de instrucciones recuperadas.

Las proyecciones se limitan a campos destinados al contexto. Se descartan bloques que contienen indicadores reconocibles de credenciales, tokens, claves privadas o interfaces de evaluación. Si aparecen en la entrada imprescindible, se devuelve `entrada_sensible` y el diagnóstico carece del texto original. El patrón reconoce formatos comunes. La autorización previa del dato y la separación de mensajes del futuro consumidor siguen siendo necesarias para reducir el riesgo de inyección.

## Ejemplos y verificación

Las pruebas reproducen ocho situaciones con datos de prueba.

| Caso | Qué verifica |
| --- | --- |
| A | Alias «la dos» y mediciones de B-102 con hora, unidad y dato nulo. |
| B | Pregunta por la unidad y respuesta anterior conservadas desde la revisión SQLite. |
| C | Citas de cavitación `TX350-D §8.9` y `MBP-4 §6.11`, con aplicabilidad indeterminada preservada. |
| D | Dos valores de límite y unidad conservados en una contradicción sin reemplazar el dato oficial. |
| E | PostgreSQL inaccesible con fuente identificada y degradación de la memoria. |
| F | Recorte de series y fallo explícito cuando el contexto esencial excede el presupuesto. |
| G | Delimitadores y cambios de rol conservados como datos y exclusión del bloque que contiene una credencial. |
| H | Rechazo de capturas de otra nota o de una revisión remota desalineada. |

```bash
python -m pytest -q tests/test_context_builder.py
```

Para repetir la integración, indica en `NOTA_ID` una nota autorizada que ya esté sincronizada. El ejemplo consulta esa memoria, un equipo y hasta dos citas. En PowerShell, con las variables de conexión del rol de aplicación ya exportadas, reutiliza la imagen y el volumen del agente. Se montan el volumen y los archivos en modo de solo lectura.

```powershell
$imagen = docker inspect --format '{{.Image}}' pia3-agente
$montajes = ConvertFrom-Json -InputObject (docker inspect --format '{{json .Mounts}}' pia3-agente)
$volumen = ($montajes | Where-Object { $_.Destination -eq '/app/data_runtime' }).Name
$src = (Resolve-Path src).Path
$ejemplo = (Resolve-Path scripts/ejemplo_contexto.py).Path
docker run --pull never --read-only --cpus=0.5 --memory=256m --network planta-lan `
  --mount "type=volume,source=$volumen,target=/app/data_runtime,readonly" `
  --mount "type=bind,source=$src,target=/contexto/src,readonly" `
  --mount "type=bind,source=$ejemplo,target=/contexto/ejemplo_contexto.py,readonly" `
  -e PYTHONPATH=/contexto/src -e PIA3_DB_PATH=/app/data_runtime/seguridad.sqlite3 `
  -e POSTGRES_HOST -e POSTGRES_PORT -e POSTGRES_DB -e POSTGRES_USER -e POSTGRES_PASSWORD `
  -e POSTGRES_CONNECT_TIMEOUT -e PIA3_RAG_CORPUS_ID `
  $imagen python /contexto/ejemplo_contexto.py --nota-id $env:NOTA_ID --equipo B-102 --consulta 'cavitación TX-T07'
```

La prueba Docker del 27 de noviembre de 2026 recuperó B-102 del catálogo, `MBP-4 §6.1` y `MBP-4 §6.11` del corpus habitual, y una versión con una respuesta sincronizada en PostgreSQL y SQLite. El JSON de contexto midió 4.952 bytes y 1.596 tokens aproximados. Se reutilizó la imagen del agente. El contenedor temporal tuvo límites efectivos de 0,5 CPU y 256 MiB. El stack habitual del alumno y ese contenedor suman 3,5 CPU y 3,25 GiB. El techo incluyendo el PostgreSQL aislado de las integraciones es de 4 CPU y 3,75 GiB. [Ejemplo de solo lectura](../scripts/ejemplo_contexto.py)

Otra consulta de cavitación combinó `MBP-4 §6.11` y `TX350-D §8.9` con la misma memoria y equipo. El JSON midió 5.764 bytes y 1.713 tokens aproximados. El ejemplo admite que la nota provenga de un reloj simulado adelantado y usa como fecha de evaluación el máximo entre la hora de lectura, el timestamp de la nota y el de la captura. Cada marca original se conserva.

La suite habitual con el corpus oficial dio 541 aprobados y 19 omitidos, con 15 pruebas nuevas del Context Builder. El grupo de pruebas del Context Builder, PostgreSQL, memoria y retrieval ejecutado en el volumen aislado del Issue #25 dio 50 aprobados. La integración del catálogo contra el simulador obtuvo 1 aprobado. También pasaron `docker compose --env-file .env.example config --quiet` y `git diff --check`.

La regresión P2 del PR #28 verificó una consulta real de B-102 con 60 muestras informadas, 3 recibidas y `truncado=true`. Al omitir por presupuesto el bloque de muestras, el JSON conservó la página con los mismos metadatos y los estados `truncada_origen` y `omitido_presupuesto`. La suite final obtuvo 559 aprobados y 19 omitidos, con 18 regresiones nuevas. [Observación P2](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/pull/28#discussion_r4716873882)

## Integración futura

LangChain podrá recibir las reglas como instrucciones y el resto como datos estructurados, con el tokenizer autorizado para medir el presupuesto del mensaje completo. El Issue #29 deberá aplicar el presupuesto global por nota, el corte y el timeout del flujo. Los circuitos operativos siguen usando sus propias interfaces. La evaluación semántica y la fusión híbrida siguen pendientes. [Plan](PLAN.md)
