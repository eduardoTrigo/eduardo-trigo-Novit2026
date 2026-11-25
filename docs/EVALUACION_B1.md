# Evaluación oficial B1 · Issue #13

El 24 de noviembre de 2026, de 22:05:21 a 22:12:55 en Buenos Aires, el corrector ejecutó `martes-tranquilo` y `fuga-sin-enlace` y terminó con código **0**. La nota fue **75,0/100**, con **47,26 puntos** y **63 de 93 puntos de rúbrica medidos**. El resultado oficial es **APRUEBA**, con mínimo 60 y ambas eliminatorias superadas. B1 obtuvo **5,83/10**, frente a 0/10 en las dos corridas anteriores. [Issue #13](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/13)

## Identidad de la corrida

| Referencia | Valor |
| --- | --- |
| Rama y HEAD del agente | `13-feat-deterministic-classification-b1` · `bb440f284c3afba5f9e30a9b0510e4c82d8cd8cd` |
| Copia del agente | Instantánea con cambios locales y archivos nuevos, incluida la corrección de la cola MQTT de 5A |
| SHA-256 del manifiesto de 48 archivos de runtime y build | `c03557011baa909470ead2e925b2a40e20a75cd13c174f1cfb64ff263b3f17fa`, igual al de 5A |
| Simulador y corrector | `ab65b95e911731351dba395817a74a8cc60da2b2` |
| Imagen del agente en ambos escenarios | `sha256:10177d5e2228dc3cf35e3e0c0c418d8463d4e9c3e9dd75780d3a557cd1f5a6f6` |
| Agente por escenario | `6322cd1a448d` en `martes-tranquilo` y `88b011179961` en `fuga-sin-enlace` |
| Configuración | `PIA3_ENABLE_OUTBOX=1`, época `issue13-5b-20261124-220350`, 2 CPU, 2 GiB, contrato `0.2.0` |

La imagen cargó Whisper `base` sin red con la caché verificada. El corrector se ejecutó con `--api http://127.0.0.1:18100 --sin-construir --escenarios martes-tranquilo fuga-sin-enlace`. Se conservaron el reloj por eventos de la rúbrica (×1800 y ×5) y sus 60 s finales de gracia. La instantánea del agente carece de metadatos Git propios. `workspace.json`, `huella-5a.json` e `imagen-verificada.json` permiten identificar el contenido exacto.

## Puntaje oficial y comparación

| Criterio medido | Peso | Issue #9 | Issue #11 | Issue #13 |
| --- | ---: | ---: | ---: | ---: |
| A1 · Cobertura de borradores | 5 | 0 | 3,19 | 3,19 |
| A2 · Confirmación al primer intento | 10 | 0 | 5,83 | 5,83 |
| A3 · Parte final correcto | 8 | 0 | 5,11 | 5,11 |
| A4a · Preguntas ante ambigüedad | 4 | 0 | 4,00 | 3,00 |
| A4b · Evitar preguntas adicionales | 3 | 3,00 | 2,79 | 2,79 |
| B1 · Detección operativa | 10 | 0 | 0 | **5,83** |
| B2 · Alerta de seguridad | 10 | 10,00 | 10,00 | **10,00** |
| E1 · Sigue operando sin enlace | 3 | 3,00 | 3,00 | 3,00 |
| E2 · Pendientes visibles | 3 | 1,50 | 1,50 | 1,50 |
| E3 · Sin duplicados | 4 | 4,00 | 4,00 | 4,00 |
| F1 · Mensajes válidos | 2 | 2,00 | 2,00 | **2,00** |
| F2 · Latido regular | 1 | 1,00 | 1,00 | **1,00** |
| **Nota reescalada** | **100** | **38,9** | **67,3** | **75,0** |

Sin datos para D1, D2, F3 y G1. G2 y G3 están inactivos en la rúbrica. Se superaron `sin_alerta_de_seguridad` y `sistema_mudo`. A4a perdió un punto frente al Issue #11. Las comparaciones corresponden a imágenes y corridas distintas. [Issue #9](EVALUACION_ENTREGA1.md) · [Issue #11](EVALUACION_ISSUE11.md)

## Reconstrucción de B1

| Escenario | Esperadas | Correctas | Incorrectas | Partes confirmados | `Indeterminado` | Eventos B1 de parte | Eventos B1 de sensor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `martes-tranquilo` | 18 | 12 | 6 | 7 | 2 | 12 | 0 |
| `fuga-sin-enlace` | 4 | 2 | 2 | 2 | 1 | 2 | 0 |
| **Total** | **22** | **14** | **8** | **9** | **3** | **14** | **0** |

Los 14 eventos B1 fueron `SinNovedad`. Los dos ratios por escenario, 12/18 y 2/4, se promedian antes de multiplicar por el peso de B1. Las dos SQLite capturadas contienen trabajos y decisiones para los nueve partes confirmados. Los 14 eventos exportados quedaron enviados y figuran en el registro válido. El registro oficial contiene una alerta S1, de tipo `AlertaSeguridad`, que pertenece a B2. La corrida oficial careció de escalados B1 y, por ello, tampoco produjo tendencias ni fotos asociadas a un escalado B1.

| Escenario | Nota | Equipo esperado | Etapa del error y evidencia |
| --- | --- | --- | --- |
| `martes-tranquilo` | `NOTA-00005` | TK-301 | Parte confirmado. Nivel de 5 m con decisión `Indeterminado`, motivo `sin_limites`. |
| `martes-tranquilo` | `NOTA-00008` | B-103 | Revisión manual. La observación de reserva contiene el error de STT «Dreserbar». |
| `martes-tranquilo` | `NOTA-00010` | TK-301 | Parte confirmado con decisión `Indeterminado` para el nivel de 5 m. El filtro del mismo parte sí produjo su evento. |
| `martes-tranquilo` | `NOTA-00011` | B-101, B-102, F-201A | Revisión manual. La extracción preservó la fila del filtro y el resto de la nota quedó sin resolver. |
| `fuga-sin-enlace` | `NOTA-00004` | TK-301 | Parte confirmado con decisión `Indeterminado` para el nivel de 5 m. B-102 sí produjo su evento. |
| `fuga-sin-enlace` | `NOTA-00006` | B-103 | La extracción conservó «parada en reserva». La nota cualitativa quedó en revisión manual, sin confirmación definitiva. |

Los ocho casos esperaban `SinNovedad`. Cinco decisiones faltaron en tres notas sin confirmación. Las otras tres corresponden a TK-301, cuyo nivel carece de límite en el catálogo usado. La evidencia de esta corrida ubica los faltantes en extracción, confirmación o aplicabilidad del límite. Las 14 decisiones exportables de los partes aparecieron en MQTT. La política de normalidad para una lectura local sin límite y el flujo de confirmación de partes exclusivamente cualitativos quedan para revisión humana.

## Seguridad y recursos en la corrida oficial

| Escenario | Válidas | Inválidas | Duplicados | Latidos | Intervalo real | CPU máxima observada | Memoria máxima observada |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| `martes-tranquilo` | 30 | 0 | 0 | 9 | 29,7593–30,2951 s | 116,38 % | 278,8 MiB |
| `fuga-sin-enlace` | 12 | 0 | 0 | 5 | 29,6444–30,1780 s | 93,35 % | 277,1 MiB |

La alerta S1 de `NOTA-00001` llegó a los **3,0153 s reales** y el aviso de tablet a los **3,0155 s**, ambos durante el corte. El límite de B2 es de 30 s. La alerta y el aviso fueron las dos publicaciones registradas durante el corte. Hubo cero falsas alarmas y los latidos cumplieron el máximo de 60 s entre publicaciones que mide F2. El corte transcurrió entre las 09:10 y las 12:10 simuladas. La corrida del Issue #11 había observado 2,5307 s y 2,5308 s para esa alerta y aviso. Los recursos son máximos muestreados de Docker, en 19 y 12 muestras respectivamente. La CPU de Docker expresa el uso respecto de un núcleo.

En `martes-tranquilo` hubo dos errores `database is locked` al inicio. Uno interrumpió la lectura de estado persistido de una muestra de sensor y el otro interrumpió un intento de encolado B1. El encolador siguió activo y los 14 eventos exportables terminaron enviados en el conjunto de ambos escenarios. El registro carece de evidencia para cuantificar el efecto de la interrupción sobre la ventana de sensor o identificar qué escritura causó los bloqueos. [Worker de sensores](../src/sistema/sensor_worker.py) · [Worker de encolado](../src/sistema/b1_event_worker.py)

## Ensayos complementarios de sensores

Dos proyectos Compose desechables ejecutaron la misma imagen del agente y un broker Mosquitto real. Las tramas MQTT, el reloj simulado, el catálogo, el estado, el historian y los medios se sirvieron como fixtures controlados. El motor del simulador oficial se utilizó en la corrida oficial. Los ensayos complementarios ejercitaron el runtime, SQLite, Whisper y los publicadores reales con esas entradas.

| Comprobación | Resultado del entorno controlado |
| --- | --- |
| Pico y anomalía sostenida | Un pico aislado quedó sin evento. La anomalía de caudal de FT-401 inició a las 15:03 simuladas, se confirmó a las 15:05 y se publicó como `Escalado`. |
| Reinicio y recuperación | El reinicio durante la anomalía conservó su ID. Las lecturas normales cerraron el episodio a las 15:09; el desvío siguiente abrió un episodio con ID distinto a las 15:14. |
| Saltos de reloj y historian | El segundo episodio usó muestras históricas servidas por HTTP entre las 15:10 y las 15:14 para corroborar el intervalo. También se observaron consultas de enriquecimiento. |
| Estado operativo | La presión de succión baja de B-103 quedó suprimida mientras estaba en reserva parada. El episodio inició a las 15:18 con la entrada en marcha y se confirmó a las 15:20. Un estado vencido quedó como `estado_operativo_no_corroborado`. |
| Saturación MQTT | Una ráfaga de 1.500 tramas produjo 1.244 descartes de ingreso registrados y una discontinuidad. Los latidos del segundo ensayo mantuvieron intervalos de 29,9977 a 29,9988 s reales durante la prueba. |
| Tendencia y foto | El primer ensayo agotó la espera en la confirmación y la búsqueda de la foto. En un segundo proyecto, con respuesta `Confirmo` y fixture de foto controlado, se guardaron 69 bytes de foto, una serie de 181 muestras y tres referencias con estado ausente. El escalado y la evidencia quedaron asociados al mismo ID de parte. |
| Recuperación de publicación | Con los publicadores detenidos por configuración, el evento quedó encolado en SQLite. Al recrear el agente con publicación activa se envió el mismo payload, con el mismo ID. Las pérdidas o demoras del ACK se cubrieron mediante pruebas automatizadas con dobles. |

SQLite devolvió `integrity_check=ok` en ambos ensayos, con archivos de 176.128 y 172.032 bytes. Se observaron envíos en los carriles de tablet y B1. Las dos lecturas puntuales de recursos fueron 5,26 % de CPU y 191,3 MiB, y 1,96 % y 244,8 MiB. Los logs HTTP registraron descargas y consultas, y las capturas MQTT guardaron las publicaciones.

### Seguridad durante los ensayos complementarios

En ambos ensayos se procesaron cuatro audios sintéticos (S1–S4) después de saturar el ingreso MQTT. Ninguno produjo alerta o aviso, de manera que el límite de 30 s quedó sin validar para estas entradas. En la repetición se usaron WAV sintéticos sin ruido de planta. Whisper transcribió S1, S2 y S3 con omisiones o sustituciones en las expresiones de riesgo. Para S4 transcribió «Hay una persona herida, una persona está lesionada». Las dos expresiones quedaron fuera de los patrones actuales. La reproducción directa de las cuatro transcripciones con el detector devolvió cero condiciones. El ensayo caracteriza un problema con estas entradas y no aísla el efecto de la carga B1 sobre la latencia de seguridad. [Patrones de seguridad](../src/sistema/security_detector.py)

La corrida oficial sí verificó S1 con carga B1 y corte externo. La suite cubre S1–S4 con entradas de texto, historian bloqueado y espera de ACK de B1 con el cliente compartido. [Pruebas del worker de sensores](../tests/test_sensor_mqtt.py) · [Pruebas de publicación](../tests/test_b1_publication.py)

## Respaldo, aislamiento y preservación

Se validaron los TAR de los seis volúmenes mediante comparación contra sus orígenes, SHA-256, extracción y comparación en un entorno auxiliar. El respaldo SQLite online y el frío coincidieron en SHA-256, con `integrity_check=ok`, 137 notas y outbox vacía. Los hitos de pausa y reanudación quedaron separados por aproximadamente 45 s. Al cierre coincidieron los seis contenedores, sus IDs, imágenes, horas de arranque, montajes, redes y volúmenes. Los binds de grabaciones conservaron el manifiesto. SQLite siguió íntegra y conservó los 137 IDs de notas originales. El escenario `libre` avanzó y la API registró un heartbeat original a las 22:24:48 de Buenos Aires.

Los 336 archivos del volumen de medios seguían presentes. Seis WAV tenían otro SHA-256 en la primera revisión y diez en el cierre. En los seis primeros, las fechas y las notas visibles en la API coincidieron con la reemisión de notas de `libre` después de reanudar el simulador. Este escenario vuelve a generar audios en rutas basadas en el ID de nota. Los cuatro cambios adicionales se observaron mientras seguía avanzando. El TAR verificado conserva el contenido anterior y se mantuvo el estado activo de la planta.

El adaptador probó el rechazo del Compose original y acotó la ejecución del corrector a los proyectos desechables. Se verificaron puertos de loopback, redes y volúmenes separados, binds de la copia de solo lectura y ausencia del socket Docker en el simulador aislado. Antes de cada retirada del agente de evaluación se verificaron su etiqueta de proyecto, imagen, red y volúmenes y se capturó la SQLite con verificación de integridad. Los resultados crudos y la rúbrica se conservaron.

Se comprobó la retirada de los contenedores y redes de los cuatro proyectos aislados. Se archivaron y validaron los 52 archivos del volumen de medios aislado antes de retirarlo por su nombre y etiqueta de proyecto. La imagen evaluada y los artefactos se conservaron para inspección. Los volúmenes anónimos sin asociación comprobada con la corrida quedaron fuera de la limpieza.

## Regresiones y revisión de cierre

La suite final con `.venv` y un directorio temporal nuevo terminó con **367 aprobadas y 0 fallidas** en 27,35 s. El plugin pytest-asyncio emitió un aviso sobre el alcance futuro por defecto del bucle. Durante 5B solo se editaron documentos del repositorio. Se preservaron los cambios acumulados de las fases anteriores.

El Issue requiere revisión humana antes de un PR de cierre. Quedan pendientes la pérdida de cobertura A4a en `NOTA-00011`, las notas cualitativas y multiequipo sin confirmación, la política de normalidad para nivel sin límite, los bloqueos SQLite y la cobertura de seguridad por voz. También falta evidencia oficial de escalados de partes y sensores. PostgreSQL 16 con pgvector y memoria de conversación sigue como próxima prioridad después del cierre de B1. [Plan](PLAN.md)

## Evidencias locales

Las rutas de esta sección son relativas a `L:\Novit2026\PIA3-eval\issue13-5b-20261124-220350`.

| Evidencia | Archivos |
| --- | --- |
| Corrector oficial | `resultados/resultado.json`, `resultados/informe.md`, `resultados/meta.json`, los dos JSON y logs por escenario, `corrector-comando.json`, `corrector.stdout.log`, `corrector.stderr.log` y `corrector-exit.json` |
| Identidad y diagnóstico | `huella-5a.json`, `workspace.json`, `imagen-verificada.json`, `contenedores-evaluacion.json`, `captura-*.json`, `sqlite-escenario-*.sqlite3`, `analisis-b1.json` y `recursos.jsonl` |
| Respaldo y aislamiento | `respaldo/verificacion.json`, `inventario-original.json`, `redes-volumenes-originales.json`, `compose-evaluacion-verificado.json`, `docker-aislado.log` y `restauracion-verificada.json` |
| Ensayos complementarios | `complementaria/resultado.json`, `complementaria-control/resultado.json`, `complementaria*/mqtt.json`, `complementaria*/servicios.log` y sus configuraciones |
| Verificación final | `medios-originales-diferencias.json`, `verificacion-cierre-original.json`, `limpieza-volumen-identificado.json`, `medios-evaluacion.tar` y `pytest-final.log` |
