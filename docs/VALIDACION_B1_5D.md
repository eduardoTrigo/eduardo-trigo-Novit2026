# Validación dirigida de seguridad y sensores · Issue #13, fase 5D

La fase 5D compara la línea de base `bb440f284c3afba5f9e30a9b0510e4c82d8cd8cd` con el workspace de la misma rama y los cambios acumulados hasta 5C. La evaluación oficial corresponde a 5B y conserva su nota de **75,0/100**, B1 **5,83/10** y B2 **10/10**. [Issue #13](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/13) · [Evaluación 5B](EVALUACION_B1.md) · [Diagnóstico 5C](DIAGNOSTICO_B1_5C.md)

## Identidad y aislamiento

| Componente | Identidad |
| --- | --- |
| Imagen base del código de `bb440f2` anterior a B1 | `sha256:beadbb1b94bba809bf416d06d20d87c29cfe0cd0fdc693a1a453ce114795c3ea` |
| Imagen del workspace con las correcciones de 5C | `sha256:591994566e55d1ad48ee48161d2747dc5e788958983953ac150d1122eb9d58bb` |
| Dependencias y caché de las dos imágenes | Imagen verificada de 5B, `sha256:10177d5e2228dc3cf35e3e0c0c418d8463d4e9c3e9dd75780d3a557cd1f5a6f6` |
| Configuración del agente | 2 CPU, 2 GiB, `PIA3_ENABLE_OUTBOX=1`, Whisper `base` en CPU con `int8`, español y caché local en modo offline |
| Proyecto Compose | `issue13-5d-20261124-230111`, broker en `127.0.0.1:62659`, HTTP de fixtures en la red interna |

El build sin red usó el código del commit y una copia de los archivos Python del workspace. Cada manifiesto se verificó dentro de su imagen. El código del adaptador y del servicio de transcripción coincide en ambas versiones tras normalizar los finales de línea. Se verificaron el Compose resuelto, las etiquetas e IDs de contenedores, redes, montajes y volúmenes antes del arranque. El agente recibió solo los montajes de su variante y los archivos de prueba de acceso de solo lectura. Se usaron una SQLite inicial nueva, nombres de sistema distintos y un ID de nota único por variante.

El archivo de medios de evaluación, el TAR del simulador y el respaldo de medios originales carecían de los cuatro WAV del control de 5B, generados en `/tmp/audio` de un contenedor retirado. La receta archivada permitió generar una sola copia de cada audio con la voz Piper `es_MX-ald-medium`, la misma frase y la misma preparación de WAV. Los brazos de 5D recibieron los mismos bytes entre sí. También se generaron dos WAV de negación. Los SHA-256, tamaños y frases de referencia se guardaron junto a las copias.

## Condiciones del ensayo

Se usaron el agente, Mosquitto, SQLite, el detector y el publicador reales, con HTTP y muestras de planta controlados. El harness externo registró la recepción MQTT, el hash del audio descargado, la entrada y salida de STT, la decisión del detector, el commit SQLite, los acuses registrados, las publicaciones observadas y la actividad de la cola. El registro de los fixtures y las trazas por brazo permiten reproducir las comparaciones. La variante de referencias devolvió una transcripción predefinida tras validar el mismo WAV. Sus tiempos corresponden al control diagnóstico.

| Variante | Código y carga de entrada |
| --- | --- |
| Base | Commit `bb440f2`, Whisper real, sin tráfico de sensores. |
| Reposo | Código actual, Whisper real, cola de sensores sin tráfico. |
| Cadencia | Código actual, Whisper real, 15 series cada 0,5 s real y 60 s simulados por ciclo. Incluye las pruebas de aceleración y contención. |
| Ráfaga | Código actual, Whisper real, una llamada de estado demorada, 1.500 tramas sin pausa y otras 450 por nota. |
| Referencias | Código actual, carga de cadencia y doble de STT para las cuatro referencias de riesgo y los dos controles negativos. |

## Seguridad y comparación de versiones

Latencias en segundos reales desde la emisión MQTT hasta la recepción de **alerta / aviso** por el observador. «Sin publicación» indica que la nota llegó a SQLite y se completó, con cero alertas materializadas.

| Variante | S1 | S2 | S3 | S4 | Controles negativos |
| --- | --- | --- | --- | --- | --- |
| Base `bb440f2`, Whisper real | Sin publicación | Sin publicación | Sin publicación | Sin publicación | 0/2 falsas alarmas |
| Actual en reposo, Whisper real | Sin publicación | Sin publicación | Sin publicación | **2,726 / 2,744** | 0/2 falsas alarmas |
| Actual a cadencia, Whisper real | Sin publicación | Sin publicación | Sin publicación | **2,589 / 2,595** | 0/2 falsas alarmas |
| Actual en ráfaga, Whisper real | Sin publicación | Sin publicación | Sin publicación | **2,745 / 2,780** | 0/2 falsas alarmas |
| Actual a cadencia, STT inyectado | 0,956 / 0,977 | 0,180 / 0,199 | 0,570 / 0,588 | 1,402 / 1,419 | 0/2 falsas alarmas |

En los cuatro brazos con Whisper real, las transcripciones para cada WAV coincidieron. El detector recibió estos resultados.

| Audio | Transcripción de Whisper | Resultado |
| --- | --- | --- |
| S1 · Proyección de agua a presión | «¿Hay un chorro de agua presión? ¿Salé agua presión en la bomba?» | Omisión de «a» en la expresión de riesgo. Las cuatro notas quedaron sin alerta ni aviso. |
| S2 · Olor a gas | «hay oloracas, se siente oloracas» | El texto perdió «olor a gas». Las cuatro notas quedaron sin alerta ni aviso. |
| S3 · Humo o fuego | «hay un moe fuego, está saliendo un muro de la bomba» | El detector no recibió una expresión de riesgo reconocible por sus reglas. Las cuatro notas quedaron sin alerta ni aviso. |
| S4 · Persona lesionada | «Hay una persona herida, una persona está alesionada.» | La versión anterior omitió «persona herida». La actual detectó y publicó S4 en todas las variantes de audio. |
| N1 · Lesión negada | «No hay una persona herida, la operadora es el...» | Cero alertas. Se conservó la negación de lesión. |
| N2 · Negación coordinada | «Voy un o ni una persona herida, la persona es... TENCER» | Cero alertas. Se conservó «ni una persona herida». |

Las 31 notas se recibieron y completaron. Se comprobaron los hashes del audio al descargarlo y las decisiones antes de guardar. Para las alertas positivas de la versión actual se observó también el aviso de tablet y el estado `enviado` en SQLite. Los controles inyectados S1–S4 verificaron la cadena desde la descarga hasta MQTT. En N1 y N2 quedó un carácter de sustitución en la palabra «está» del texto auxiliar de la receta, después de la cláusula de negación. Las dos cláusulas negativas se conservaron en el doble. Las frases de control y las salidas textuales efectivas están archivadas.

Esta matriz aporta evidencia de que la corrección S4 funcionó con el audio probado y bajo las tres condiciones de carga. Las pérdidas de S1–S3 fueron iguales incluso antes de B1. La comparación carece de evidencia de una regresión de seguridad atribuible a B1 para esas entradas. La cobertura de S1–S3 con audio real sigue pendiente. Para cerrar esa brecha hace falta una muestra de voz representativa, sin editar las transcripciones de Whisper. Tampoco se midió la frecuencia poblacional de falsas alarmas a partir de dos controles.

## Sensores a cadencia representativa, aceleración y contención

El brazo de cadencia emitió 960 tramas en 64 ciclos de 15 series. Las 12 tramas dirigidas de aceleración y contención elevaron el total a **972**. El observador MQTT y el callback registraron las 972, el worker procesó las 972 y la cola terminó vacía, sin descartes ni invalidaciones por generación. El máximo muestreado fue de 15 elementos. El pico FT-401 de las 15:01 simuladas quedó sin episodio. Se abrieron y recuperaron tres episodios, con IDs distintos.

| Caso FT-401 | Primera lectura | Apertura | Recuperación |
| --- | --- | --- | --- |
| Caudal bajo sostenido a cadencia representativa | 15:06 | 15:08 | 15:14 |
| Salto de 360 s simulados entre dos lecturas vivas | 16:10 | 16:16 | 16:19 |
| Lectura bloqueada por SQLite y recuperada por el reintento | 16:20 | 16:22 | 16:25 |

Las horas de la tabla son del 20 de noviembre simulado, en UTC. La segunda ventana utilizó cinco muestras del historian de fixtures entre las dos lecturas vivas. Los tres episodios publicaron un `Escalado` válido con su ID persistido. Las lecturas anómalas adicionales del primer episodio no generaron un segundo ID. La cola y la ventana conservaron sus valores de 256 entradas, tres muestras y 120 s simulados.

Una segunda conexión mantuvo `BEGIN EXCLUSIVE` en la base desechable. La tercera lectura del último episodio agotó el timeout de SQLite en **5,012 s**, con `SQLITE_BUSY` (código 5). El harness liberó el bloqueo al observar esa excepción y el reintento de 5C abrió el episodio con la muestra original. Otros workers registraron errores SQLite transitorios durante el bloqueo. Las siete publicaciones de este brazo terminaron enviadas. Una nota S4 ingresó durante el bloqueo y produjo alerta y aviso en **8,277 s** y **8,293 s**, dentro del límite de 30 s. El latido observado mantuvo un intervalo de **29,999 s**.

### Ráfaga artificial

| Serie de pruebas | Emitidas | Observadas en broker y callback | Procesadas | Cola llena | Invalidadas tras el ingreso | Pico muestreado de cola |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cadencia y secuencias dirigidas | 972 | 972 | 972 | 0 | 0 | 15 |
| Ráfaga con Whisper real | 4.201 | 4.201 | 2 | 2.660 | 1.539 | 256 |
| Cadencia con referencias de STT | 960 | 960 | 960 | 0 | 0 | 20 |

La ráfaga tuvo una trama de preparación, 1.500 de saturación inicial y 2.700 emitidas junto con las seis notas. Se rechazaron 2.660 por cola llena, lo que creó discontinuidades. De las 1.541 aceptadas, dos alcanzaron el procesamiento y 1.539 quedaron en generaciones descartadas. La última muestra mostró cola vacía y cero errores de procesamiento. La secuencia de la ráfaga carecía de un episodio de desvío sostenido esperado. Produjo S4 con latencia de **2,745 / 2,780 s** y dos latidos con una brecha de **29,997 s**. Los descartes de esta saturación deliberada caracterizan el límite de ingreso observado. La cadencia representativa y el ensayo dirigido de contención procesaron todas sus tramas.

## Latido, publicaciones y recursos

| Variante | CPU máxima | Memoria máxima | Muestras | Brecha de heartbeat |
| --- | ---: | ---: | ---: | ---: |
| Base | 193,18 % | 316,7 MiB | 8 | 30,001 s |
| Reposo | 189,58 % | 317,1 MiB | 8 | 29,999 s |
| Cadencia | 200,79 % | 323,4 MiB | 10 | 29,999 s |
| Ráfaga | 195,74 % | 318,1 MiB | 7 | 29,997 s |
| Referencias | 13,47 % | 197,0 MiB | 7 | 29,999 s |

La CPU es el máximo observado por Docker respecto de un núcleo, con límite configurado de 2 CPU. El caso de referencias prescinde del cómputo de transcripción. Cada variante aportó dos latidos y una brecha alrededor de 30 s, por debajo del máximo F2 de 60 s en el período observado. El observador validó el contrato para los mensajes de `sistema/#`. Se registraron cero publicaciones inválidas o con ID lógico duplicado. Las cinco SQLite pasaron el chequeo de integridad y las 20 filas de outbox de toda la matriz quedaron enviadas, sin errores de publicación persistidos. El bloqueo forzado demostró contención también en el acceso SQLite del publicador, seguida de recuperación.

## Preservación y evidencias

El inventario final de solo lectura coincidió en los seis contenedores originales, con los mismos IDs, imágenes, arranques, estados, montajes y redes. La comparación ordenó las listas de montajes. La verificación de los datos del entorno original se limitó a su identidad y a la ausencia de montajes originales en el entorno desechable. Compose retiró sus siete contenedores, dos redes y cinco volúmenes verificados. Se conservaron las dos imágenes etiquetadas de 5D para reproducir la comparación y las copias SQLite y WAV en el directorio de evidencias.

Las rutas se resuelven bajo `L:\Novit2026\PIA3-eval\issue13-5d-20261124-230111`.

| Evidencia | Archivos |
| --- | --- |
| Identidad, audio y proyecto | `preparacion.json`, `imagenes/*/Dockerfile`, `harness/referencias.json`, `wav/*.wav`, `compose-verificado.json`, `contenedores-aislados.json` |
| Resultados y datos por variante | `resultados.json`, `*/mqtt.json`, `*/traza.jsonl`, `*/cola.jsonl`, `*/agente.log`, `*/agente.sqlite3`, `*/recursos.json` |
| Carga y contención | `cadencia/lock.json`, `servidor-http.log`, `broker.log`, `ejecutar.py`, `harness/*.py` |
| Preservación y pruebas | `original-antes.json`, `original-despues.json`, `preservacion.json`, `limpieza-verificada.json`, `pytest-final.log` |

## Validación final

La suite completa ejecutada con `.venv` y un directorio temporal nuevo dio **394 aprobadas, 0 fallidas** en 31,99 s. Se verificaron los archivos de runtime por SHA-256 contra el manifiesto de la imagen actual. El único cambio del repositorio en 5D fue este informe. [Diagnóstico y pruebas de 5C](DIAGNOSTICO_B1_5C.md)

## Recomendación

Una última corrida oficial, con autorización y el aislamiento verificado, aportaría una comprobación de regresiones de integración para el Issue. Conviene revisar primero un ensayo de voz representativa que verifique S1–S3, ya que la rúbrica aplicada en 5B observó solo una condición de seguridad S1. Para cerrar el Issue quedan la revisión humana de esa cobertura y la última comprobación oficial. TK-301 conserva su decisión interna `Indeterminado`. PostgreSQL 16 con pgvector sigue como prioridad posterior al cierre.
