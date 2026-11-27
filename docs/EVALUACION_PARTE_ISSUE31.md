# Evaluación dirigida del parte por voz · Issue #31

El preflight verificó la rama limpia `feat/31-voice-part-improvements`, la base `eadc7b0781393229e851f6e01398094cb106a063`, Python 3.12.10, Docker 28.1.1, `planta_contrato` 0.2.0 y 577 tests aprobados con 19 omitidos. La suite utilizó `tmp/rag-corpus-local.zip` con identidad de corpus `83cfec46e9d62b88fcf0ee2767f794fa0fd64ce7277ad41e1d0ccfd20991d1a2`. Los datos habituales quedaron fuera de las pruebas.

## Diagnóstico de las capturas

| Escenario y nota | Evidencia e impacto | Resultado dirigido |
| --- | --- | --- |
| martes · 00006 | La referencia dice 112 kg/cm². El STT real entregó 102, fuera del histórico archivado de B-101 (110,9–115,6) pero dentro de sus límites operativos (95–125). El operador corrigió a 112. | La pregunta de rango se conserva para el texto real. La referencia produce borrador directo en ambas versiones. |
| martes · 00008 | El STT produjo «parada en Dreserbar». Tras resolver el equipo, la política anterior derivaba a revisión cualquier parte íntegramente cualitativo. | B-103 conserva la observación de parada y el fragmento original, que sigue sin acreditar «en reserva». Se obtiene un borrador confirmable por una persona. |
| martes · 00011 | El STT unió descarga y rodamiento y perdió el artículo antes de «bomba». La unidad quedó como «pc y» y las cifras de descarga fueron 102 y 1610, frente a 112 y 1600 en la referencia. Antes se conservaba solo F-201A a 1,3. | Se conservan las dos filas B-101 y la del filtro. La pregunta de equipo recupera A4a en el replay. Después de elegir B-102 queda una pregunta de unidad que el operador virtual deja pendiente. Con la referencia se obtienen las cuatro filas y se confirma. |
| fuga · 00006 | «La bomba está parada en reserva». La elección de B-103 funcionaba y el estado anterior impedía el borrador cualitativo. | Borrador con `estado`, valor y unidad nulos. El agente admite confirmación humana. El diálogo oficial devuelve una solicitud de mostrar lo anotado porque filtra las filas sin valor. |
| fuga · 00001 | La voz reportó una proyección de agua y una foto tomada desde lejos. El registro histórico contiene S1 y la referencia de la foto. | Se conserva como evidencia de nota con revisión manual del parte. La condición de seguridad sigue en su propio circuito. |

Las cuatro notas históricas sin borrador fueron martes 00008 y 00011, y fuga 00001 y 00006. Se identificaron con el registro del diálogo en el Issue #11 y se reprodujeron con la evidencia de STT de 5B. La transcripción archivada de 5B y la del Issue #11 difieren en la nota multiequipo. Las causas observadas incluyen STT, segmentación, incertidumbre de equipo y unidad, y la política anterior del diálogo cualitativo. El replay conserva la falta de una medición reportable en la nota de seguridad. Las fuentes inaccesibles y el presupuesto se ejercitaron con regresiones específicas. [Issue #11](EVALUACION_ISSUE11.md) · [Evidencia 5B](DIAGNOSTICO_B1_5C.md)

## Comparación en las mismas 13 notas

El procedimiento lee los reportes oficiales archivados de 5B y abre las capturas SQLite en modo de solo lectura. Ejecuta por separado el texto de referencia y el STT archivado con el catálogo oficial, fechas originales y el mínimo y máximo histórico guardados donde hay evidencia. Los turnos usan la función original del operador virtual con una semilla fija. Se conservan hasta tres interacciones. Una repetición idéntica de un turno queda fuera del cómputo. Esta medición propia de la sección A usa series resumidas y omite la temporización de MQTT, las colas y la confianza acústica. Los puntajes oficiales siguen en los informes históricos.

| Métrica | STT, antes → después | Referencia, antes → después |
| --- | ---: | ---: |
| Campos TAG correctos | 21 → 23 / 24 | 24 → 24 / 24 |
| Campos variable correctos | 21 → 23 / 24 | 24 → 24 / 24 |
| Campos valor correctos por fila identificada | 19 → 20 / 22 | 22 → 22 / 22 |
| Campos unidad correctos por fila identificada | 19 → 21 / 22 | 22 → 22 / 22 |
| Observaciones con el contenido esperado | 1 → 1 / 2 | 2 → 2 / 2 |
| Mensajes con el timestamp original | 12/12 → 16/16 | 13/13 → 15/15 |
| Notas con borrador | 9 → 11 / 13 | 10 → 12 / 13 |
| Borradores directos en el primer turno | 8 → 8 | 9 → 9 |
| Confirmaciones virtuales | 9 → 9 | 10 → 10 |
| Preguntas justificadas por el texto recibido | 3 → 5 | 3 → 3 |
| Preguntas innecesarias en el replay | 0 → 0 | 0 → 0 |
| Notas en revisión manual al terminar | 4 → 1 | 3 → 1 |
| Turnos pendientes al terminar | 0 → 3 | 0 → 2 |

Las 24 filas incluyen las dos observaciones cualitativas. La fila `condicion_insegura` pertenece al circuito S1 y queda fuera del denominador. TAG y variable se comparan como conjuntos con multiplicidad por nota. Valor y unidad requieren un par TAG/variable coincidente. Las observaciones se comparan por contenido normalizado. El propio criterio considera justificada la pregunta por 102 ante la evidencia histórica, aunque el criterio oficial la penalizó porque el audio de referencia describía una nota clara.

El archivo de 13 notas solo tiene una foto esperada para S1. La asociación por fila se evaluó con un caso controlado que menciona dos equipos y la foto de la empaquetadura de B-102. La base carecía de asociación interna y el resultado nuevo vinculó la única foto a `B-102/goteo_empaquetadura`. Dos referencias sin correspondencia explícita permanecen en el nivel de nota. La rectificación de un TAG devuelve la foto a la nota y conserva el vínculo anterior en su versión.

En PowerShell, con la captura de 5B y el repositorio oficial local, se puede repetir la comparación desde la raíz. El ZIP de la base y el directorio de salida deben ser nuevos.

```powershell
git archive --format=zip --output=tmp/base-issue31.zip eadc7b0781393229e851f6e01398094cb106a063 src
Expand-Archive tmp/base-issue31.zip tmp/base-issue31
$env:PYTHONDONTWRITEBYTECODE = '1'
$captura5B = 'L:/Novit2026/PIA3-eval/issue13-5b-20261124-220350'
$env:PYTHONPATH = 'tmp/base-issue31/src;../academia2026-tp/planta/sim'
python scripts/evaluar_parte_issue31.py $captura5B ../academia2026-tp/datos/planta.yaml
$env:PYTHONPATH = 'src;../academia2026-tp/planta/sim'
python scripts/evaluar_parte_issue31.py $captura5B ../academia2026-tp/datos/planta.yaml
```

## Verificación técnica

- **Regresiones y suite.** 605 aprobadas y 19 omitidas con el corpus local. Se verificaron la extracción mixta, las observaciones, la negación, las unidades, los conflictos, la cobertura histórica, la asociación y sus correcciones, la recuperación tras reapertura, el presupuesto, B1 y seguridad.
- **PostgreSQL aislado.** Seis integraciones aprobadas en PostgreSQL 16 con pgvector. La foto apareció en el contexto de las dos versiones proyectadas y en la parte confirmada. El binario se recuperó de SQLite por SHA-256. Se verificaron el rol de aplicación, el timeout y una operación vectorial real.
- **Docker con motor controlado.** Mosquitto real en loopback, descarga de WAV y foto por HTTP, borrador y confirmación por MQTT, evidencia recuperada de SQLite y proyección local. `ejecucion_partes` registró 11 pasos y 1.299 ms activos acumulados en la nota confirmada. S1 se publicó antes de que la foto de otra nota fallara con 404. Esa nota conservó el parte en espera de operador con 9 pasos y 32 ms activos. El presupuesto de los dos contenedores auxiliares sumó 0,95 CPU y 896 MiB, dentro del máximo agregado de 4 CPU y 8 GiB junto al agente y PostgreSQL habituales.
- **Docker con WAV archivado.** Se descargó por HTTP el audio de `martes-tranquilo` 00011, originalmente generado por el simulador, y se transcribió realmente con `faster-whisper base` desde la caché montada en solo lectura, sin red externa. Se reprodujo el STT histórico. El worker guardó tres filas inequívocas y una aclaración de equipo. Esta prueba llega hasta la aclaración inicial del audio archivado.
- **Configuración y diff.** `docker compose config --quiet` y `git diff --check` terminaron correctamente. Los procedimientos aislados conservaron sus datos y no ejecutaron el corrector.

Quedan pendientes la medición integral desde la emisión de la nota, el recorrido con voz humana real hasta la confirmación y la nueva medición oficial de A1–A4. CT-1 y RG-1 conservan su estado parcial y la autorización oficial de modelos sigue pendiente. Si la asociación es incierta, la foto queda en la nota. Si se alcanza el límite de almacenamiento, quedan la referencia, su asociación y el estado de descarga omitida. [Especificación y recuperación de evidencia](SPEC_PARTE_VOZ.md) · [Plan de Entrega 1](PLAN.md)

## Regresiones del PR #32

La revisión partió de `2b5ca1b` en `feat/31-voice-part-improvements`, con el árbol limpio. Las regresiones iniciales dieron siete fallos y tres casos de control aprobados. [Observaciones cualitativas](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/pull/32#discussion_r4719680075) · [Asociación posterior a una aclaración](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/pull/32#discussion_r4719680092)

| Entrada | En `2b5ca1b` | Con la corrección |
| --- | --- | --- |
| «Saqué foto de la tres, tiene las correas flojas» | El filtro descartaba la frase completa. | El borrador incluye `B-103/estado_correas`, valor y unidad nulos y la observación «tiene las correas flojas». |
| «Saqué foto de las correas de la tres» | La referencia quedaba fuera de las lecturas. | La referencia se conserva a nivel de nota y el parte queda sin contenido reportable. |
| «La dos 1640 pc de descarga. Saqué foto del manómetro de descarga de la dos» | La foto quedaba a nivel de nota incluso después de responder «psi». | La primera versión guarda el objetivo pendiente. Tras la aclaración, la segunda vincula la foto a `B-102/presion_descarga_local`. |

Las pruebas integradas ejercitaron el descargador real del worker con transporte HTTP controlado. Tras reabrir SQLite se resolvieron una duda de unidad y una de valor. En cada flujo se registró un solo GET, se mantuvieron la identidad, SHA-256, ruta, timestamp, MIME, tamaño, estado y evidencia, y el parte se confirmó con una respuesta explícita. Se verificaron respuestas duplicadas y desfasadas, ambigüedad, varias fotos, cambios de TAG y el límite de pasos e interacciones. Los registros previos sin un objetivo guardado siguen siendo legibles y conservan su estado.

En PostgreSQL 16 aislado, las siete integraciones aprobaron. La proyección guardó el objetivo pendiente en el primer turno y la asociación resuelta en el borrador siguiente y en el parte confirmado. El contenido binario permaneció en SQLite.

La suite completa con `PIA3_RAG_CORPUS` terminó con 628 pruebas aprobadas y 20 omitidas. La integración nueva se ejecutó en la pasada separada de PostgreSQL. También pasaron `docker compose config --quiet` y `git diff --check`.

La ejecución del evaluador existente sobre las mismas 13 notas, tanto con STT archivado como con referencia, dio la misma salida para `2b5ca1b` y para la corrección. Con STT se mantuvieron 11/13 borradores, 23/24 en TAG y variable, 20/22 en valor, 21/22 en unidad y 9 confirmaciones virtuales. La prueba de regresión y la evaluación dirigida usan casos y métricas propios de este incremento. Los informes históricos conservan sus resultados oficiales.

### Referencias espaciales

Sobre `a9343d7`, las regresiones iniciales reprodujeron 12 fallos. El patrón interpretaba «de la dos» en «foto de un goteo cerca de la dos» como evidencia del equipo fotografiado. También tomaba un segundo equipo mencionado solo como ubicación como otro posible objetivo, lo que impedía vincular fotos con un objetivo directo. [Observación del PR #32](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/pull/32#discussion_r4719976778)

El análisis excluye los complementos espaciales antes de identificar equipo y variable. «Foto de un goteo al lado de la dos» queda a nivel de nota, sin objetivo pendiente. «Foto del goteo de la dos al lado de la tres» conserva el objetivo B-102. Si una coordinación deja incierto el alcance del complemento, la foto queda a nivel de nota. Las pruebas cubren TAG, alias, aclaración de unidad, confirmación y recuperación con el mismo binario y SHA-256.

La evaluación dirigida de las 13 notas dio salida idéntica antes y después con el STT archivado y con texto de referencia. Con STT se mantuvieron 11/13 borradores, 23/24 en TAG y variable, 20/22 en valor, 21/22 en unidad y 9 confirmaciones virtuales. Las asociaciones históricas ya persistidas quedan fuera del reprocesamiento.

La suite con `PIA3_RAG_CORPUS` obtuvo 653 aprobadas y 22 omitidas. Las nueve integraciones aisladas de PostgreSQL incluyeron la proyección de la referencia espacial a nivel de nota tras la aclaración y confirmación. También pasaron `docker compose config --quiet` y `git diff --check`.
