# Mini especificación · Clasificación determinista B1

## 1. Objetivo y alcance

Clasificar cada equipo de un parte confirmado y detectar episodios sostenidos en sensores de PIA-3. La decisión se toma en planta con reglas explícitas y datos locales. Los incrementos 1 a 4 del [Issue #13](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/13) implementan las reglas, la ejecución persistente de partes y sensores, la evidencia y la publicación MQTT, sobre `13-feat-deterministic-classification-b1` en `bb440f2`.

## 2. Requisitos oficiales y trazabilidad

Se consultaron el **Enunciado TP**, p. 3, sección B, y el **Anexo Entrega 1**, p. 3, sección B, en `L:\Novit2026`. La [copia oficial del simulador](https://github.com/NovitSoftware/academia2026-tp/tree/ab65b95e911731351dba395817a74a8cc60da2b2) estaba limpia en ese commit. El contrato instalado es `0.2.0` y su módulo coincide por SHA-256 con la copia oficial.

| Fuente | Requisito o medición | Diseño |
| --- | --- | --- |
| Enunciado B y Anexo B | Eventos locales deterministas por parte confirmado y desvío de sensor, con límites de planta. Escalar anomalías sostenidas con parte, fotos y tendencia de las últimas horas. Tratar la parada en reserva como normal. | §§ 4–14 |
| Issue #13 | Decisión por equipo, parada por mantenimiento normal, tratamiento explícito de entradas insuficientes y persistencia sin duplicados lógicos. | §§ 5–6, 10–14 |
| Contrato oficial y guía MQTT/HTTP | Modelos estrictos respecto de campos extra, tópicos, referencias, IDs idempotentes y servicios locales. `/api/verdad` queda reservada a evaluación y desarrollo controlados. | §§ 4, 12–15 |
| Rúbrica B1 y `clasificacion_eventos` | Vale 10 puntos. Cuenta aciertos por nota y equipo contra los ítems esperados, omite las notas de seguridad y vincula por `nota_ids`. Agrega anomalías por equipo mediante OR y usa límites inclusivos para las dos variables configuradas. | §§ 5–7, 17 |

El scorer toma como normales las observaciones literales de parada en reserva y mantenimiento. Acepta un `SinNovedad` genérico para una nota sin ítems esperados y también como coincidencia de normalidad por equipo. Para normalidad, un `Escalado` concurrente del mismo equipo invalida el acierto. El Issue exige que el agente identifique el equipo en cada evento de parte. El scorer recorre notas, sin comprobar la confirmación local, ni medir episodios autónomos, ventanas o evidencia de sensores. Los casos de parte sin contenido y de observaciones que el agente no puede confirmar pueden incidir en el puntaje. El puntaje por sí solo deja esas condiciones de aceptación sin verificar.

Fuentes del simulador: [modelos](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/contrato/planta_contrato/__init__.py), [contrato de integración](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/docs/contrato.md), [rúbrica](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/correccion/rubrica.yaml#L74) y [scorer](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/correccion/puntuar.py#L207).

## 3. Estado actual relevante

- El entrypoint de Compose es [`security_integrated.py`](../src/sistema/security_integrated.py). Cablea la recepción de recorridas, respuestas, sensores y energía, el latido y los workers de seguridad, partes y B1. Las publicaciones tienen tres carriles, con publicadores independientes de seguridad, tablet y B1. [Arquitectura](ARQUITECTURA.md) · [Plan](PLAN.md) · [Decisiones](DECISIONES_TECNICAS.md)
- [`WorkerPartes._procesar_respuesta`](../src/sistema/part_worker.py#L114) interpreta el turno vigente. [`AlmacenPartes.aplicar_respuesta`](../src/sistema/part_store.py) revalida correlación, fase y duplicados bajo `BEGIN IMMEDIATE`. La confirmación queda firme al guardar en un mismo commit el estado `confirmado`, `respuestas_parte.resultado='confirmada'` y el trabajo B1. Aceptar un candidato en una aclaración habilita un borrador, que requiere su propia confirmación.
- [`AlmacenPartes.obtener`](../src/sistema/part_store.py#L199) une `partes` con `notas.payload`. Recupera filas oficiales, versión, ID del mensaje vigente, contexto de lecturas y resoluciones, además de nota, hora y URLs de fotos. La respuesta aplicada conserva su payload y, cuando corresponde, el texto de STT. Las fotos disponibles son referencias a nivel de nota. La extracción cualitativa tiene límites y puede derivar contenido a revisión manual. [Especificación de parte](SPEC_PARTE_VOZ.md)
- [`ClientePlanta`](../src/sistema/part_context.py) comparte el acceso y la caché de `/api/planta` con B1. El worker B1 requiere la colección de sensores y vuelve a consultar si la caché del diálogo carece de ella. Las aclaraciones siguen usando el rango histórico y comparaciones estrictas fuera del intervalo. B1 aplica su comparador inclusivo de alarmas.
- El Issue #13 obtuvo 75,0/100 y B1 5,83/10, con B2 10/10, F1 2/2, F2 1/1 y las dos eliminatorias superadas. Cinco decisiones esperadas procedían de notas sin confirmación. Las tres restantes quedaron indeterminadas por nivel de tanque sin límite aplicable. [Evaluación oficial](EVALUACION_B1.md)

## 4. Entradas y salidas

| Interfaz | Datos y uso propuesto |
| --- | --- |
| Clasificador puro | `clasificar_parte(filas, catalogo)` recibe filas `FilaParte` y un catálogo con `sensores` y, si está disponible, `equipos`. Devuelve una tupla de `DecisionB1` ordenada por equipo, con resultado y evidencia inmutables. La evidencia conserva valores, unidades y observaciones de origen, su equivalencia canónica cuando aplica y los motivos por fila o cláusula. Los valores rechazados se registran con una representación textual estable. [Implementación](../src/sistema/part_classification.py) |
| Parte confirmado en SQLite | El trabajo guarda una copia JSON de las filas finales y referencias a la nota, el parte, la versión, el mensaje aceptado y la respuesta definitiva. El payload de la nota conserva la hora de observación y las fotos. El payload de la respuesta conserva la hora de confirmación. |
| `planta/sensores/#` | QoS 0. El worker valida cada `LecturaSensor(tag, variable, valor, unidad, ts, calidad)` y verifica que el tópico exacto `planta/sensores/{tag}/{variable}` coincida con el cuerpo. |
| `planta/energia` | QoS 1, retenido por el simulador. Los cambios de `EstadoEnergia` interrumpen la continuidad. Las muestras posteriores a un corte conocido no acreditan anomalía ni recuperación mientras persista el corte. |
| `GET /api/planta` | Catálogo de equipos, unidades, límites y manómetros. Proveer al clasificador una instantánea validada del catálogo con huella reproducible. |
| `GET /api/estado` | Usar `ts`, energía y estado operativo de bombas y filtros. Aceptar la instantánea para una muestra del mismo instante o de menos de 10 s simulados antes de la instantánea. Un cambio de energía observado invalida la caché. Falta validar la cobertura de esta tolerancia. |
| `GET /api/historian/{tag}/{variable}?desde=&hasta=` | Serie local con `ts` y `valor`. La unidad se obtiene del catálogo. Consultas acotadas para corroboración de continuidad y para contexto. |
| `sistema/eventos` | Construir `EventoSistema(id, ts, tipo, equipo, origen, parte_id, nota_ids, resumen)` mediante el paquete oficial. En parte, usar `ts=nota.ts`, `origen="parte"` y referencias al parte y sus notas. En sensor, usar el `ts` de la última muestra que confirma la ventana, `origen="sensor"` y, en ausencia de una nota originante, `parte_id=null` y `nota_ids=[]`. |

## 5. Decisiones para partes confirmados

Evaluar todas las filas del mismo equipo, incluyendo observaciones y contexto operativo explícito del parte. Usar el estado informado a la hora de la nota. Guardar las razones por fila y emitir como máximo un evento por equipo y versión confirmada.

| Entrada del equipo | Decisión aprobada |
| --- | --- |
| Parte pendiente, rechazado, en revisión manual o sin contenido | Fuera de B1 hasta una confirmación operativa con equipo identificado. |
| Alguna anomalía sustentada, incluso con otra fila incompleta | `Escalado` con todas las razones sustentadas y registro de los faltantes. |
| Parada en reserva o mantenimiento explícitos, sin anomalía independiente ni evidencia relevante indeterminada | `SinNovedad`. Una lectura baja esperada por parada se identifica como tal solo cuando corresponde al equipo, variable y condición documentados. |
| Magnitudes evaluables compatibles con sus límites y observaciones relevantes normales | `SinNovedad`. |
| Falta dato indispensable, unidad incompatible, contradicción o texto no resuelto, sin una anomalía independiente sustentada | `Indeterminado`, exclusivamente interno, con motivo por evidencia. El reintento por falta de catálogo corresponde a la integración. |
| Lectura local válida sin límite oficial ni señal cualitativa anómala | Registrar la lectura como contexto sin evaluarla como normal. Emitir `SinNovedad` solo si hay evidencia evaluable normal y la lectura sin límite no deja un indicio pendiente. Si es la única evidencia, el resultado queda indeterminado. |

Una lectura anómala confirmada por el operador es evidencia del parte. La ventana de persistencia descrita en § 9 se aplica al flujo automático de sensores. Una observación de parada no anula un disparo, una pérdida, un ruido anómalo u otro hallazgo independiente. Una afirmación de marcha por sí sola aporta contexto. Si el mismo parte afirma marcha y parada o disparo para el equipo sin distinguir tiempos, la condición operativa queda indeterminada.

## 6. Observaciones cualitativas

Reutilizar la normalización de texto del parte para tildes y mayúsculas y aplicar patrones por variable y cláusula, con alcance explícito de las negaciones. Conservar el texto original. Reconocer variantes inequívocas de parada en reserva y por mantenimiento como normales. Un disparo, un equipo que no arranca, un nivel bajo de aceite, correas flojas o dañadas, golpeteo metálico, ruido anormal de rodamiento y pérdida observada por empaquetadura son candidatos a escalado cuando están afirmados y asociados al equipo. Registrar una negación explícita como ausencia del síntoma correspondiente. Las dudas y contradicciones quedan pendientes de revisión. Los indicadores de S1–S4 siguen su detector propio.

El [operador oficial](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/planta/sim/planta_sim/operador.py#L165) emite esos tipos de hallazgo. Sus umbrales internos de generación de frases no son límites de planta. La extracción mantiene unida la cláusula «parada, en reserva» antes de separar observaciones, de modo que el clasificador recibe la condición completa en la fila confirmada. Para goteo anotado sin magnitud, distinguir una pérdida calificada como anormal de una descripción ambigua de goteo que no permite decidir. Un rodamiento descrito como caliente puede evaluarse cualitativamente. Una medición IR sola carece de límite B1 en el catálogo inspeccionado. El vocabulario de campo aún requiere validación.

## 7. Límites oficiales y equivalencias

Valores de referencia del [`datos/planta.yaml`](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/datos/planta.yaml) inspeccionado. Leerlos dinámicamente desde `/api/planta` o de una copia oficial versionada aprobada. Cargar por `(tag, variable)` y validar unidades, unicidad y consistencia del rango. Una alarma se activa al alcanzar o cruzar `alarma_baja` o `alarma_alta`, como en el scorer. Guardar `disparo` como evidencia de severidad. En sensores, la persistencia sigue siendo necesaria.

| Equipos | Variable | Unidad | Baja | Alta | Disparo |
| --- | --- | --- | ---: | ---: | ---: |
| B-101, B-102, B-103 | `presion_succion` | `kg/cm2` | 0,9 | — | — |
| B-101, B-102, B-103 | `presion_descarga` | `kg/cm2` | 95 | 125 | — |
| B-101, B-102, B-103 | `temperatura_aceite` | `degC` | — | 80 | 90 |
| B-101, B-102, B-103 | `vibracion` | `mm/s` | — | 7,1 | 11,2 |
| F-201A, F-201B | `presion_diferencial` | `kg/cm2` | 0,15 | 1,5 | — |
| FT-401 | `caudal` | `m3/d` | 2200 | — | — |

- Para el mismo equipo, `presion_descarga_local` equivale a `presion_descarga`. El manómetro local es `kg/cm2` en B-101, `psi` en B-102 y `bar` en B-103, según catálogo. El clasificador requiere que la unidad resuelta figure explícitamente en la fila. La resolución de esa unidad corresponde al flujo previo de parte.
- `presion_diferencial_local` equivale a `presion_diferencial` en filtros. El generador oficial y el parte actual usan `presion_diferencial`. Para otras variables, exigir coincidencia del tag, variable y dimensión física.
- Para comparar presiones, reutilizar los factores del código actual: `psi / 14.2233` y `bar / 0.980665` dan `kg/cm2`. El scorer usa `1.01972` para bar, un redondeo del factor. Preservar valor y unidad originales y comparar el valor normalizado sin redondeo de presentación. Probar igualdad en unidad canónica y los dos lados del umbral después de convertir. Una unidad desconocida o de otra dimensión queda indeterminada.
- Las mediciones IR, nivel de tanque, goteo y otras lecturas locales sin límite asociado conservan su contexto. El rango histórico ayuda a interpretar la lectura y el diálogo. Los límites de alarma provienen del catálogo de planta.

## 8. Procesamiento de sensores

El [cliente MQTT](../src/sistema/mqtt_client.py) se resuscribe al conectar. En el callback se verifican el tópico y el tamaño y se ofrece el paquete a una cola de 256 mensajes sin esperar. El [worker de sensores](../src/sistema/sensor_worker.py) valida, normaliza y persiste en su propio hilo. Ante saturación, paquetes sobredimensionados o desconexión, el worker invalida el lote anterior y corta las ventanas de todas las series de la época. Conserva los episodios abiertos y registra las pérdidas de ingreso detectadas. MQTT QoS 0 también admite pérdidas que el agente no puede observar directamente.

El worker extrae la trama antes de sincronizar su generación. Si se anuncia una discontinuidad durante la extracción, la primera trama de la generación vigente puede iniciar una ventana nueva. Una prueba reproduce ese orden de operaciones y verifica la apertura posterior del episodio sin reutilizar muestras anteriores al corte.

Cada equipo y variable mantiene una serie con un solo episodio activo. Una presión baja de una bomba parada o en mantenimiento y un diferencial bajo de un filtro que corrobora `en_cambio` suspenden la evaluación de esa lectura. Una baja dependiente de marcha sin estado corroborado queda indeterminada. El disparo conserva la aplicabilidad de la alarma. Las alarmas independientes, como caudal bajo del colector o vibración alta, conservan su evaluación durante una parada normal. El rol inicial y los alias carecen de efecto sobre el estado operativo. El catálogo oficial se comparte con el worker de partes. El estado se consulta solo cuando la aplicabilidad lo requiere, se reutiliza entre muestras contemporáneas y se limita a una petición cada 0,5 s reales.

## 9. Ventanas y persistencia de sensores

El bucle del [simulador](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/planta/sim/planta_sim/app.py#L169) duerme nominalmente 0,5 s reales. Publica al final del avance, con timestamps de la física en pasos de 10 s simulados. A ×1 pueden llegar repeticiones del mismo instante. A ×3600 un tick puede cubrir unos 30 min simulados sin muestras MQTT intermedias. El historian muestrea dentro del avance, nominalmente cada 60 s simulados y hasta 45 días. La [lógica de planta](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/planta/sim/planta_sim/modelo.py#L189) modela 90 s de arranque de la reserva, y constantes de respuesta de 60 s para vibración y 120 s para velocidad.

- **Inicio**. Se aprobaron provisionalmente al menos tres instantes válidos distintos, en la misma dirección y sin interrupciones, durante al menos **120 s simulados** entre el primero y el último. La duración cubre dos intervalos del historian y supera el arranque nominal de la reserva. Falta calibrarla con reproducción controlada, incluyendo alarmas legítimas de corta duración.
- **Continuidad**. Separación máxima propuesta de **120 s simulados** entre muestras de evidencia, suficiente para tolerar un intervalo nominal del historian faltante. Una lectura recibida dentro de rango o un corte conocido interrumpe la secuencia. Dos puntos separados por un salto grande no acreditan continuidad por sí solos.
- **Aceleración**. Ante un hueco mayor entre puntos vivos compatibles que ya tienen una candidatura o recuperación en curso, el worker puede consultar historian hasta el nuevo punto, con un máximo de **10 min simulados**. Si el último punto vivo queda fuera del tramo, solo se considera la secuencia continua final que termina en la muestra MQTT actual. Un valor normal, nulo o incompatible anterior a esa secuencia corta la continuidad y queda fuera de la evidencia. Para unir dos puntos vivos dentro del intervalo se exige cobertura continua entre ambos. Se valida la dirección, la coincidencia con los puntos vivos, la separación entre timestamps y los conflictos en un mismo instante. Las lecturas intermedias de historian carecen de estado operativo propio. Por eso se omite la consulta cuando la ventana requiere corroborar marcha. En el arranque se esperan muestras MQTT. La consulta usa el offset de planta, timeout de 2 s, un máximo de 32 filas aceptadas, una pausa de 1 s real por serie entre consultas y una pausa global de 2 s reales ante fallos HTTP o de formato.

## 10. Picos, calidad, duplicados y desorden

- Un punto fuera de rango seguido por un valor dentro de rango reinicia la candidatura sin evento. Los umbrales usan `ts` de planta con zona horaria, normalizados a UTC para orden e identidad. Las pausas con timestamps repetidos no avanzan ventanas.
- Validar JSON con `LecturaSensor` y exigir valor numérico finito de origen, unidad compatible, timestamp con offset y equipo y variable del catálogo. `sin_dato`, nulos, muestras inválidas atribuibles por tópico y pérdidas detectadas interrumpen la candidatura o recuperación. Si falla el catálogo, el worker espera 2 s reales antes de reintentar con otro paquete. Los paquetes recibidos durante esa espera no aportan continuidad.
- Usar `(época, tag, variable, ts)` para deduplicar. Un reenvío equivalente no cambia contadores. Si llega un valor distinto para el mismo instante antes de cerrar la decisión, marcar la evidencia contradictoria e interrumpir la serie. Los conflictos posteriores quedan registrados sin reescribir eventos publicados.
- Procesar en orden creciente por serie. Una muestra tardía anterior a la última procesada queda fuera de la decisión en curso y se registra. El historian puede corroborar un hueco antes de confirmar una transición, bajo las condiciones de § 9. El determinismo cubre la misma secuencia de entrada, estado y catálogo, con una política explícita para las llegadas tardías.

## 11. Recuperación y episodios nuevos

Al confirmar la ventana, el worker abre un episodio y registra un único `Escalado` interno. Conserva la evidencia de apertura, los extremos de las lecturas anómalas y el último punto de la serie. Se requieren al menos tres muestras válidas dentro de límites y **120 s simulados** de continuidad para cerrar. En bombas y filtros, la recuperación requiere marcha corroborada. Una vuelta breve a rango, un cambio de lado de la alarma, pérdida de datos, mantenimiento o falta de energía interrumpen la recuperación. El mismo episodio permanece activo hasta corroborar el retorno a operación normal.

Al cerrar el episodio, un nuevo tramo anómalo sostenido puede abrir otro episodio con un ID distinto. Se aprobó registrar inicialmente la recuperación en SQLite. Una publicación de recuperación de origen sensor queda pendiente de revisión.

## 12. Identificadores e idempotencia

- Parte: `parte_id = PARTE-{nota_id}` en la base actual. La clave del trabajo es `(nota_id, version)` y la de la decisión y su exportación es `(nota_id, version, equipo)`. El ID público usa un hash con prefijo `B1-PARTE-` sobre origen, recorrida, hora de nota en UTC, parte, versión y equipo. La identidad queda ligada a la evidencia persistida de la recorrida y es independiente de la configuración de época de sensores.
- Sensor: ID con prefijo B1 y hash de época, origen, equipo, variable, dirección e instante inicial de la secuencia corroborada. Fijar ese instante al registrar la apertura. Excluir hora real, intentos, redondeo del resumen y enriquecimientos del ID.
- `PIA3_SENSOR_EPOCH` identifica la corrida de sensores y debe mantenerse al reiniciar el agente. Su valor por defecto es `local`. El contrato MQTT carece de identificador de corrida. Si el simulador reinicia el reloj, el agente trata las lecturas anteriores como tardías dentro de la misma época. Una corrida independiente necesita una nueva época configurada. La identidad de los partes históricos conserva sus reglas actuales.

## 13. Persistencia, atomicidad y reinicios

La migración crea `trabajos_b1` y `decisiones_b1` junto con el esquema de partes. La confirmación definitiva inserta el trabajo y su instantánea en la misma transacción que actualiza el parte y la respuesta. Cualquier fallo de inserción revierte los tres cambios. La migración deja los partes históricos fuera de la cola.

El worker reclama cada trabajo mediante una reserva de 30 s reales, cierra la conexión y consulta el catálogo antes de clasificar. Guarda las decisiones y sus motivos y cambia el estado a `clasificado` en un segundo commit. Un fallo revierte el lote completo. Una reserva vencida puede recuperarse con el mismo identificador y un nuevo número de intento. La escritura final exige el intento vigente, de modo que un consumidor anterior no puede duplicar las decisiones.

Los fallos técnicos de lectura, catálogo, clasificación o escritura se reintentan hasta tres intentos, con esperas de 1 y 2 s reales después de los dos primeros fallos registrados. Si el último intento falla o su reserva vence, el trabajo queda `fallido` con el motivo técnico. Si el catálogo está disponible y la evidencia de un equipo es insuficiente, ese equipo queda `Indeterminado` y el trabajo queda `clasificado`. El clasificador guarda la huella del catálogo junto con el trabajo. Las decisiones anteriores a esa migración conservan los límites aplicados en su evidencia.

El [almacén de sensores](../src/sistema/sensor_store.py) agrega `series_sensor_b1` y `episodios_sensor_b1`. Guarda el avance de una serie junto con la apertura del episodio y su decisión `Escalado` con publicación `pendiente` en una única transacción. El cierre guarda la ventana de recuperación. Se conservan evidencia original y canónica, umbrales, huellas de límites, revisiones y timestamps. La clave de la serie y su revisión impiden sobreescrituras obsoletas. El ID del episodio es único. Una huella distinta reinicia la ventana y conserva el episodio activo. El catálogo se carga en caché desde planta, por lo que un cambio de configuración operativa requiere recargar el proceso.

Al iniciar el worker se marca una discontinuidad, aunque exista candidatura persistida. El episodio activo y su ID sobreviven. Las ventanas se rearman desde nueva evidencia viva.

El [traspaso B1](../src/sistema/b1_event_store.py) consume decisiones guardadas de partes definitivos y episodios de sensores pendientes. Valida el payload con `EventoSistema` antes de abrir la transacción. En un commit agrega el payload al carril B1 de outbox, asocia la evidencia mínima en `eventos_b1` y, para sensores, cambia `publicacion` de `pendiente` a `encolado`. La fila de evento y la clave única de parte impiden repetir el traspaso. El fallo revierte todas las escrituras. Una decisión interna `Indeterminado` permanece en `decisiones_b1`.

La outbox es la fuente del estado de envío posterior al traspaso. `estado_publicacion` devuelve `encolado` durante la espera de envío o del ACK, `fallo_tecnico` ante un error de envío o intento agotado y `publicado` solo después de guardar la confirmación del ACK. Antes del traspaso, la decisión sigue pendiente en su tabla de origen. La recuperación mueve los envíos interrumpidos a pendientes. Una caída tras el ACK y antes de confirmar en SQLite puede provocar reenvío con el mismo ID y el mismo payload. Se aplican los tres intentos del publicador y la reactivación manual existente para agotados.

Para una futura integración PostgreSQL, las reglas y la construcción de mensajes siguen separadas del SQL. La atomicidad actual requiere que la evidencia y la outbox compartan la transacción SQLite. Al migrarlas se deberán preservar claves únicas, reclamo y confirmación por carril, y sustituir `BEGIN IMMEDIATE`, `rowid` y las consultas SQLite de JSON y timestamps por mecanismos equivalentes.

## 14. Contexto y evidencia de los escalados

El registro local vinculado a `EventoSistema.id` conserva las filas del equipo, referencias de nota y fotos, observaciones, reglas y límites aplicados. En sensores incluye la ventana, dirección, calidad, estado operativo, valores originales y canónicos, extremos y huella de límites. El [enriquecedor](../src/sistema/b1_evidence.py) puede añadir como contexto el último parte confirmado y clasificado del mismo equipo dentro de las tres horas simuladas previas. El evento público de sensor conserva `parte_id=null` y `nota_ids=[]`.

El enriquecimiento consulta hasta cuatro series pertinentes por escalado durante las últimas **tres horas simuladas**, con un máximo de 240 muestras aceptadas por serie y timeout HTTP de 2 s. Conserva el offset de la fuente y usa la zona del catálogo para episodios históricos sin offset. Valida timestamps, valores, duplicados y cobertura. Registra calidad desconocida cuando el historian carece de ese dato. Una tendencia ausente, incompleta o inaccesible puede reintentarse hasta tres veces.

Las fotos se vinculan a su nota. La evidencia disponible en el contrato actual deja la atribución por fila indeterminada. Se descargan hasta tres por escalado desde rutas relativas bajo `/media/fotos/` en la API de planta, con validación de MIME, firma y un máximo de 512 KiB por foto. El contenido queda en `fotos_evento_b1`. Se registra si la foto está recuperada, ausente, inválida, inaccesible u omitida por límite.

El [constructor](../src/sistema/b1_events.py) resume el hallazgo y agrega el ID recuperable. El enriquecimiento ocurre en otro hilo, fuera del callback y de las transacciones. Conserva las referencias y el detalle en SQLite con reintentos independientes. El payload público queda fijo al encolar.

## 15. Seguridad, MQTT, heartbeat y outbox

Los workers B1 de partes, sensores, traspaso y enriquecimiento usan hilos separados. Las llamadas HTTP ocurren con las conexiones SQLite de lectura y escritura cerradas. Las pruebas bloquean el historian de sensores y de enriquecimiento mientras la seguridad guarda y publica las alertas S1–S4 y sus avisos, y se construye el latido. [`OutboxSeguridad`](../src/sistema/security_outbox.py) conserva el reclamo y la confirmación por carril. [`WorkerOutbox`](../src/sistema/outbox_worker.py) publica por [MQTT QoS 1](../src/sistema/outbox_publisher.py).

La migración agrega `es_b1` con valor histórico por defecto 0. Cada instancia filtra por `es_borrador` y `es_b1` en el ciclo de envío y de recuperación. El publicador B1 espera un ACK por vez, fuera de SQLite y en un hilo distinto al de seguridad. Una prueba mantiene pendiente ese ACK en el cliente compartido mientras seguridad confirma su envío. El heartbeat conserva su cadencia de 30 s reales. `PIA3_ENABLE_OUTBOX` habilita los publicadores. Con valor 0, las decisiones y su evidencia pueden guardarse y quedar encoladas. La corrida con broker real superó B2, F1, F2 y las eliminatorias, y registró dos bloqueos SQLite en B1. El ensayo de saturación mantuvo la cadencia del latido y dejó pendiente la validación de S1–S4 por voz bajo esa carga. [Mediciones](EVALUACION_B1.md)

## 16. Implementación incremental mediante TDD

Cada incremento parte de una prueba que falla por el comportamiento faltante, pasa con un cambio acotado y termina con revisión del código y regresiones pertinentes.

1. **Reglas puras implementadas.** Clasificación por equipo, normalización, límites inclusivos, observaciones y resultado insuficiente. La suite unitaria usa un subconjunto de límites de planta y entradas sintéticas, y verifica también que el clasificador responda a límites distintos suministrados por el llamador. [Pruebas](../tests/test_part_classification.py)
2. **Confirmación de partes implementada.** Trabajo duradero para la versión confirmada y decisiones por equipo, con pruebas de rollback, recuperación y correlación. [Pruebas](../tests/test_part_classification_integration.py)
3. **Sensores implementados.** Máquina pura, persistencia recuperable, suscripción MQTT, cola acotada y worker. Validación con datos sintéticos y SQLite temporal. [Reglas](../tests/test_sensor_detection.py) · [Persistencia](../tests/test_sensor_store.py) · [Integración](../tests/test_sensor_mqtt.py)
4. **Evidencia y publicaciones implementadas.** Traspaso atómico, evidencia por ID, enriquecimiento, migración del carril B1 y publicación QoS 1. Pruebas de recuperación antes y después del ACK, de persistencia y de independencia de seguridad. [Pruebas](../tests/test_b1_publication.py)
5. **Integración y resultados.** Se ejecutaron la suite, la reproducción controlada de sensores y los dos escenarios oficiales autorizados. La revisión de cierre incluye la caída de A4a, la confirmación de notas cualitativas y la política de nivel de tanque sin límite. [Evidencia y resultados](EVALUACION_B1.md)

## 17. Matriz de pruebas y aceptación futura

| Área | Casos y resultado esperado |
| --- | --- |
| Límites y equipo | Parte con bomba y filtro genera decisiones independientes. Probar umbrales altos y bajos inclusivos, ambas equivalencias locales, psi y bar, unidad incompatible y variable sin límite. Repetir con la misma entrada y obtener los mismos resultados. |
| Observaciones | Parada en reserva y mantenimiento normales. Una pérdida en el mismo equipo la convierte en escalado. Negación, disparo, observación desconocida y contradicción tienen resolución explícita. |
| Confirmación | Pendientes, rechazados y revisión manual quedan fuera. Una corrección y su confirmación posterior clasifican solo la versión final. Una respuesta duplicada, obsoleta o de aclaración no crea trabajos nuevos. |
| Ventana | Pico aislado y dos instantes separados por un salto sin cobertura no escalan. Tres instantes y 120 s sostenidos escalan una vez. Reproducir velocidad ×1, reloj repetido, aceleración y un histórico que corrobora o contradice el tramo. |
| Calidad y estado | Probar duplicados y conflictos, llegada tardía, NaN, nulos, `sin_dato`, tópico incorrecto, pérdida de cola y energía, estado no corroborado, bomba en reserva que entra en marcha y bypass de filtro. Sin datos suficientes, no hay decisión de normalidad. |
| Reapertura | Recuperación insuficiente mantiene el episodio. Recuperación sostenida lo cierra y un desvío posterior genera otro ID. Reiniciar con episodio abierto o trabajo reclamado conserva la identidad. |
| Transacción y contrato | Forzar rollback de confirmación, escritura B1 y encolado. Simular caída antes y después del ACK. Validar serialización de ambos orígenes con `EventoSistema` y comprobar referencias y estados de evidencia faltante. |
| Regresión | Ejercitar S1–S4, borradores, reconexión MQTT, avisos, latido durante carga y continuidad local con enlace externo caído. Validar B2 (alerta y aviso en hasta 30 s reales), F1, F2 y las dos eliminatorias con evidencia autorizada. El 10/10 de B1 sigue siendo objetivo de evaluación. |

## 18. Decisiones aprobadas y validación pendiente

- Aprobados la clasificación por equipo, límites inclusivos, equivalencias compatibles, precedencia de la anomalía sustentada, paradas esperadas y el resultado interno indeterminado ante información insuficiente. Validar con partes reales la cobertura de los patrones cualitativos y la disponibilidad de variables, unidades y cláusulas completas en las filas confirmadas.
- Para sensores, validar la apertura provisional de 120 s y tres muestras, los parámetros de recuperación, continuidad, consulta histórica, tolerancia de estado y ventana contextual de tres horas. Revisar con cargas y velocidades representativas. Evaluar la publicación de recuperación y la cobertura requerida para aplicar el estado operativo histórico.
- Validar la identidad de corrida, la capacidad de la outbox compartida, el volumen de evidencia y la política de retención y acceso a fotos. El registro de recuperación en SQLite, el vínculo de evidencia por ID y la exclusión del reproceso histórico automático siguen vigentes.
- Autorizar cada incremento funcional y, por separado, la evaluación oficial en un entorno que preserve la planta y sus volúmenes.
