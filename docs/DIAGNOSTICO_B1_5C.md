# Diagnóstico y correcciones B1 · Fase 5C

La fase 5C parte de `13-feat-deterministic-classification-b1` en `bb440f2` y de las 367 pruebas de 5B. La corrida del 24 de noviembre de 2026 conserva su resultado de 75,0/100, con B1 5,83/10, B2 10/10, F1 2/2, F2 1/1 y ambas eliminatorias superadas. Los cambios de 5C se validaron con pruebas automatizadas y se cotejaron con los registros archivados. [Issue #13](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/13) · [Evaluación oficial de 5B](EVALUACION_B1.md)

| Prioridad y hallazgo | Causa y evidencia | Reproducción y cambio | Riesgo residual y validación |
| --- | --- | --- | --- |
| P0 · S4 sin alerta | «Persona herida» y «persona está lesionada» estaban fuera del patrón. La nota terminó con cero alertas. | RED en dos tests del detector y cuatro recorridos con STT inyectado. Se incorporaron las expresiones afirmativas de lesión, con cobertura de negación directa y coordinada. GREEN en los seis casos. | Medir alerta y aviso con audio real bajo carga y verificar falsas alarmas. |
| P0 · S1–S3 sin alerta | Las transcripciones archivadas perdieron expresiones de riesgo. | Los textos transcritos producen cero alertas con y sin cola saturada. Las tres transcripciones de referencia producen alerta y aviso en ambos casos. El STT y estos patrones conservan su configuración. | Comparar el mismo WAV con y sin carga para aislar reconocimiento y latencia. |
| P1 · Lectura de sensores bloqueada | `sqlite3.OperationalError: database is locked` al leer la serie. La muestra ya había salido de la cola. | RED con una base temporal bloqueada por otra conexión. La tercera muestra, necesaria para abrir el episodio, se perdía. GREEN con un reintento por `SQLITE_BUSY`. Si cambió la generación se descarta la muestra anterior. | Repetir con la cadencia de planta y medir los bloqueos persistentes. |
| P1 · Encolado B1 bloqueado | La misma excepción ocurrió al consultar un parte pendiente de encolar. | La prueba concurrente reprodujo el bloqueo y pasó con la recuperación existente. El worker encoló una sola vez al liberar la base. | El bloqueo puede retrasar el envío. Medir bajo carga simultánea. |
| P1 · TK-301 indeterminado | Tres partes confirmados registraron una lectura local válida de nivel. El catálogo carece de un límite de alarma aplicable. | Resultado y motivos comprobados en las SQLite archivadas. Sigue vigente la política aprobada. | Si planta aprueba un criterio de normalidad, revisarlo antes de implementarlo. |
| P1 · A4a en `NOTA-00011` | En 5B, Whisper fusionó «también la bomba» y cambió un número. El extractor dejó de recibir la referencia ambigua que generaba la pregunta. | Los extractores de #11 y 5B produjeron las mismas filas y dudas para cada texto. Dos pruebas caracterizan los estados sin cambiar el flujo. | Validar voz y exactitud de la pregunta en otro ensayo controlado. |
| P1 · Cero eventos de sensor y escalados oficiales | La rúbrica esperaba 22 normales de partes. El estado final de las 15 series fue compatible con operación normal o reserva. | En ambas SQLite hubo cero episodios. Los ensayos controlados de 5B sí generaron episodios. Se conservan la cola y la ventana temporal. | Falta una serie cruda completa para certificar la continuidad bajo cada escenario. |
| Preservación · Diez WAV con hash distinto | Seis reemisiones corroboradas en 5B. En los cuatro restantes solo se conserva la evidencia del cambio de hash y el archivo previo. | Los cuatro originales están en el TAR y sus notas están en las SQLite archivadas. La reemisión individual quedó sin corroborar. | Mantener los respaldos. |

## Seguridad por etapas

El segundo ensayo de 5B registró las cuatro notas en el callback MQTT, las guardó en SQLite, devolvió HTTP 200 al descargar cada WAV y completó las notas con transcripción. El pipeline valida el WAV antes de invocar al STT. Los cuatro registros terminan con `Alertas guardadas: 0`. La transacción había completado la nota con una lista vacía, de modo que para esos IDs quedaron sin materializar tanto el evento como el aviso. La auditoría MQTT mostró la ausencia aguas abajo. El resultado genérico `ok` del primer ensayo marca que se ejecutó la comprobación. Su detalle también da `faltante` en S1–S4.

| Código | Frase de referencia | Transcripción archivada |
| --- | --- | --- |
| S1 | Hay un chorro de agua a presión. Sale agua a presión en la bomba. | ¿Hay un choro de agua presión sale agua presión en la bomba? |
| S2 | Hay olor a gas. Se siente olor a gas. | Hay oloracas, se siente oloracas. |
| S3 | Hay humo y fuego. Está saliendo humo de la bomba. | Ahí un buen juego, está saliendo un modo de la bomba. |
| S4 | Hay una persona herida. Una persona está lesionada. | Hay una persona herida, una persona está lesionada. |

La matriz automatizada inyecta los ocho textos con un doble de STT y verifica recepción, validación de WAV, nota, alerta, aviso, outbox, acuse y publicaciones de contrato. Cada texto pasa por una cola sin carga y por otra saturada mediante el callback MQTT real con broker simulado. La suite también cubre seguridad mientras el hilo B1 espera al historian y mientras el publicador B1 espera el ACK. [Detector](../src/sistema/security_detector.py) · [Matriz de seguridad](../tests/test_part_integration.py) · [Concurrencia de historian](../tests/test_sensor_mqtt.py) · [ACK compartido](../tests/test_b1_publication.py)

### Procedimiento pendiente de autorización para el ensayo aislado

1. Preparar un Compose exclusivo con broker local real, servidor HTTP de fixtures, SQLite nueva por variante y la misma imagen y caché de Whisper verificadas. Registrar digest, recursos y puertos de loopback. Mantener redes, montajes y volúmenes disjuntos del entorno operativo.
2. Buscar la copia archivada de los cuatro WAV y registrar SHA-256. El control de 5B generó los WAV bajo `/tmp/audio` del contenedor retirado. Si quedan solo la receta y las frases, regenerar un conjunto con el mismo Piper y conservar los bytes y los hashes para que todos los brazos reciban WAV idénticos dentro de la nueva corrida.
3. Con igual arranque y modelo precalentado, procesar S1–S4 con el B1 habilitado y sin tráfico de sensores. Repetir con carga B1 sostenida a cadencia medida y con la ráfaga controlada de 5B. Registrar por separado el tráfico de las 1.500 tramas iniciales y las 450 adicionales por nota del control. Probar además audios de negación y notas operativas normales.
4. Repetir las comparaciones inyectando exclusivamente el resultado correcto del STT en el harness, tras descargar y validar los mismos WAV. Mantener el detector, la persistencia SQLite, el broker y el publicador íntegros. Una comparación adicional con la imagen de 5B permite medir el efecto de la corrección S4.
5. Capturar inicio real de la nota, callback, inserción SQLite, descarga, validación, resultado STT, detección, commit de evento y aviso, reclamo, ACK y recepción en el broker. Correlacionar por ID. Registrar latencias por etapa y para alerta y aviso, brechas de latido, falsas alarmas, descartes, diferencias entre tramas enviadas, recibidas y procesadas y tiempos de SQLite. Usar 30 s para alerta y aviso y 60 s como máximo entre latidos. Conservar trazas y resultados fuera del repositorio.

## SQLite y cobertura de sensores

Las dos excepciones de `martes-tranquilo` ocurrieron a las 22:05:53 y 22:05:54 de Buenos Aires del 24 de noviembre. Las consultas afectadas fueron `AlmacenSensoresB1.leer` y `AlmacenEventosB1._parte`. Ambas SQLite archivadas informan `journal_mode=delete`. Las lecturas y escrituras de los almacenes B1 usan conexiones cortas con el timeout SQLite por defecto de 5 s. La finalización de seguridad y las transacciones de selección y traspaso de outbox usan `BEGIN IMMEDIATE` donde deben reservar escritura. El sensor lee antes de consultar estado o historian y abre otra conexión al guardar serie y episodio. El encolador lee la decisión, construye y valida el evento y recién entonces abre su transacción de escritura. Los logs carecen de la identidad del escritor y del tiempo exacto de espera.

La prueba retiene `BEGIN EXCLUSIVE` en una segunda conexión y ajusta a cero el timeout solo en el test para hacer visible `SQLITE_BUSY` sin esperar 5 s. Al liberar la base, el reintento de lectura preserva la muestra y el orden si la generación sigue vigente. La espera de 0,5 s del worker es cancelable y la conexión anterior ya se cerró. Si persiste el bloqueo, se conserva el corte de continuidad del worker. Los bloqueos de escritura y el efecto de bloqueos prolongados sobre la ingestión siguen siendo riesgos. Para el encolado, la decisión duradera permanece disponible tras la excepción y el bucle vuelve a consultar después de 2 s. La prueba verificó que ambos hilos siguen vivos y que se guardan una sola fila de outbox y su evidencia. En 5B, todos los eventos exportables llegaron a MQTT y se conservaron el latido y la alerta oficial. Las dos excepciones ocurrieron en un escenario distinto al de la alerta S1, por lo que la comparación carece de una medición directa de alerta simultánea al bloqueo. [Worker de sensores](../src/sistema/sensor_worker.py) · [Worker de eventos](../src/sistema/b1_event_worker.py)

La verdad y la rúbrica midieron 22 decisiones de parte normales. La condición S1 de fuga pertenece a B2. Al final de ambos escenarios, 14 de 15 series estaban dentro de límites y la presión de descarga de B-103 se excluyó por parada corroborada. La historia archivada carece de fallas operativas registradas y las bases contienen cero episodios abiertos, cerrados o pendientes. Esto ubica la ausencia de eventos de sensores antes del traspaso a outbox. La falta de una captura de todas las muestras de entrada impide reconstruir picos, huecos y supresiones anteriores. Las ventanas requieren 120 s simulados y tres muestras, el historian se consulta de forma condicional y la cobertura existente verificó un salto con evidencia en HTTP y otro sin cobertura.

Las 1.244 pérdidas de ingreso representan **82,9 %** de las 1.500 tramas inyectadas sin pausa y coinciden con una cola inicial de capacidad 256. El contador mide descartes observados por el agente. El emisor envió QoS 0 y el ensayo carece de métricas por trama para inferir la velocidad de servicio, la pérdida previa al callback o cuántas tramas aceptadas quedaron invalidadas al cortar la generación. El resultado caracteriza una ráfaga adversa y deja pendiente medir la cadencia real. [Diseño de la ventana](SPEC_CLASIFICACION_B1.md) · [Evidencia de 5B](EVALUACION_B1.md)

## TK-301 y aclaraciones A4a

El catálogo oficial identifica `nivel` de TK-301 como lectura local y carece de una entrada de sensor de control para ella. Los tres partes guardaron `5.0 m` y el clasificador registró `sin_limites` con el catálogo disponible. El contexto del parte registra historian `sin_sensor` y límites `sin_fuente`. La consigna de 5 m y el rebalse a 7,6 m del modelo explican la simulación física. La rúbrica espera `SinNovedad` cuando la lectura no dispara una regla configurada. El catálogo deja sin definir una banda de normalidad operativa aprobada para esta lectura. Por el contrato, `Indeterminado` es interno.

La propuesta es conservar `Indeterminado` para una cifra sin criterio de dominio y solicitar una regla versionada y aprobada para nivel local si planta requiere emitir una decisión. Esa regla podría usar una banda oficial o una observación cualitativa explícita aceptada por planta. Para una regla futura se esperan las siguientes pruebas.

- Una lectura válida sin límite conserva `Indeterminado`.
- Un catálogo ausente o incompleto queda registrado con un motivo distinto.
- Los valores no finitos y las unidades incompatibles quedan indeterminados.
- Un historial disponible por sí solo conserva `Indeterminado`.
- Una observación sustentada y su negación se evalúan según la regla cualitativa aprobada.
- Si se aprueba una banda, se verifican interior, extremos y valores fuera de rango y se registra la fuente del criterio.

[Catálogo oficial](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/datos/planta.yaml) · [Rúbrica](https://github.com/NovitSoftware/academia2026-tp/blob/ab65b95e911731351dba395817a74a8cc60da2b2/correccion/rubrica.yaml) · [Semántica B1](SPEC_CLASIFICACION_B1.md)

La verdad de campo conservó el mismo texto para `NOTA-00011` en ambas corridas. En #11, el STT reconoció «112 kilogramos» y «ambien la bomba [...] 1610 pc y de descarga». El extractor conservó F-201A a 1,3 kg/cm2 y abrió una pregunta de equipo para la lectura de descarga, que todavía carecía de unidad resuelta. La primera respuesta resolvió el equipo y la segunda quedó pendiente de STT en la captura, con el parte en `aclaracion_pendiente`. En 5B, el STT dio «102 kilogramos» y «ambiana bomba [...] 1610 pc y de descarga». Se conservó la misma fila de F-201A. Las cláusulas mezcladas quedaron sin interpretar y el parte terminó en `revision_manual` sin pregunta. Evaluar ambos textos con ambos extractores dio una duda de equipo y una fila para el primer texto, y una sola fila para el segundo. La diferencia de STT explica la pérdida observada. [Prueba de caracterización](../tests/test_part_clarification.py)

## Grabaciones originales y verificación

En 5B había 336 rutas de medios antes y después. El registro de detalle con hashes, fechas y notas visibles corroboró seis reemisiones. Para `NOTA-00118`, `NOTA-00120`, `NOTA-00122` y `NOTA-00124` se comprobó que sus WAV previos están en el TAR y las notas en las copias SQLite anteriores y posteriores al corrector. El inventario final enumera sus rutas como archivos existentes con hash distinto. Los registros guardados carecen de sus versiones posteriores y de una observación individual de reemisión. El patrón es compatible con la continuidad de `libre`. La atribución de esos cuatro cambios quedó abierta. La revisión se hizo sobre artefactos archivados.

Las rutas se resuelven bajo `L:\Novit2026\PIA3-eval\issue13-5b-20261124-220350`. La comparación de A4a usó también `L:\Novit2026\PIA3-eval\issue11-20261124-085637`.

| Verificación | Evidencia |
| --- | --- |
| Resultado y etapas de 5B | `resultados/resultado.json`, `analisis-b1.json`, `resultados/martes-tranquilo.log`, `sqlite-escenario-*.sqlite3` |
| Ensayo de sensores y voz | `complementaria/resultado.json`, `complementaria-control/resultado.json`, `complementaria-control/servicios.log`, `ensayo-sensores-control.py` |
| Grabaciones | `verificacion-final-original.json`, `verificacion-cierre-original.json`, `medios-originales-diferencias.json`, `respaldo/planta_planta-media.tar` |

## Pruebas y decisión de cierre

El RED previo a los cambios tuvo **7 fallos esperados y 26 aprobadas**. El GREEN de las cinco suites específicas dio **134 aprobadas**. La suite completa con `.venv` y directorio temporal exclusivo dio **394 aprobadas, 0 fallidas**. Se añadieron 27 casos parametrizados o individuales. Pytest emitió el aviso previo de pytest-asyncio sobre el alcance predeterminado del bucle.

La próxima validación requiere autorización para el ensayo aislado con S1–S4 y carga comparable y para un caso de sensor sostenido con captura de entrada completa. La reevaluación oficial puede seguir tras revisar esos resultados, con el mismo aislamiento de 5B. El PR de cierre queda pendiente de esa evidencia y de la decisión de dominio para las lecturas locales.
