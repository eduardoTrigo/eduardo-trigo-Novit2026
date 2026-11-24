# Evaluación oficial de Entrega 1 · PIA-3

El 24 de noviembre de 2026, entre las 05:27:40 y las 05:34:04 de Buenos Aires, el corrector ejecutó `martes-tranquilo` y `fuga-sin-enlace` y salió con código `0`. Ambos escenarios terminaron. El resultado global fue **38,9/100**, por debajo del mínimo de 60. El corrector midió criterios por 63 de 93 puntos y reescaló la nota a 100. Se superaron ambas eliminatorias en esta corrida. [Issue #9](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/9)

| Referencia | Valor |
| --- | --- |
| Rama del agente | `9-test-security-scenarios` |
| Commit del agente | `1e8b7b7832b7612b5fe09a30a043f58b24560c9d` |
| Commit del simulador | `ab65b95e911731351dba395817a74a8cc60da2b2` |
| Imagen del agente observada en ambos escenarios | `sha256:f0848a8bd0307553aa1ab0273fb17e60c12d0fe58e419c7712150336004d6235` |

## Resultados

| Escenario | Estado | Mensajes válidos | Inválidos | Heartbeats válidos | Intervalos reales entre latidos |
| --- | --- | ---: | ---: | ---: | --- |
| `martes-tranquilo` | Terminado | 6 | 0 | 6 | 29,71–30,42 s |
| `fuga-sin-enlace` | Terminado | 7 | 0 | 5 | 29,64–30,35 s |
| **Total** | | **13** | **0** | **11** | Sin huecos mayores a 60 s |

- **B2 · 10/10.** `fuga-sin-enlace` registró una alerta S1 válida y su aviso de tablet. En `martes-tranquilo` no hubo casos aplicables a B2. El corrector no registró falsas alarmas en los dos escenarios.
- **F1 · 2/2 y F2 · 1/1.** Todos los mensajes recibidos validaron contra el contrato. La regularidad del latido cumplió el umbral de la rúbrica.
- **B1 · 0/10.** No se emitieron los eventos esperados `SinNovedad` o `Escalado`. Ninguna de las 13 notas recibió un borrador de parte. A1–A4a obtuvieron cero puntos. A4b obtuvo 3/3 porque el corrector no observó preguntas adicionales en las notas claras. Ese criterio aislado no acredita el flujo de borrador y confirmación.
- **Eliminatorias superadas.** `sin_alerta_de_seguridad` quedó superada con la alerta válida en `fuga-sin-enlace`. `sistema_mudo` quedó superada con publicaciones válidas en ambos escenarios.

## Seguridad durante el corte y STT

El simulador emitió `NOTA-00001` a las 08:32:02,681179 UTC. Recibió `SEG-NOTA-00001-S1` **3,0203 s reales** después y `TABLET-SEG-NOTA-00001` **3,0205 s reales** después. En ambas recepciones el enlace estaba caído. El aviso indicó aplicar el procedimiento de emergencia y avisar por radio al supervisor, con `espera_respuesta=false`.

La nota insegura usó voz sintética. El agente recibió el audio, lo transcribió localmente, persistió la nota y emitió la alerta y el aviso durante el corte. El proxy registró **25,60 s reales** de caída y no hubo latidos dentro de esa ventana. OFF-1 queda parcialmente validado. Falta comprobar el parte completo sin enlace, el estado efectivo de enlace y pendientes y la recuperación de la sincronización. Los campos del heartbeat siguen usando valores constantes.

`faster-whisper base` descargó el modelo desde Hugging Face con la caché vacía antes del inicio de cada escenario. La carga tardó **22,1 s** en `martes-tranquilo` y **20,2 s** en `fuga-sin-enlace`. Los logs muestran un aviso de acceso sin autenticación y no registran errores de STT, MQTT, HTTP ni de contrato. Siguen pendientes la autorización oficial del modelo y su configuración, el arranque sin Internet con caché vacía y la validación con voz de campo y condiciones S2–S4.

## Restauración de desarrollo

Los seis volúmenes originales coincidieron con los respaldos en frío por contenido y listado de archivos. Antes de reiniciar el agente, la SQLite original pasó `PRAGMA integrity_check` y coincidió con el respaldo de **108 notas y 0 mensajes en outbox**. Los seis servicios volvieron a funcionar con sus imágenes y montajes originales, incluidos SQLite, Whisper, medios y los dos volúmenes de Mosquitto. El simulador volvió a `libre` y recibió **cuatro heartbeats nuevos y válidos** del agente. Ambos repositorios quedaron limpios.

## Evidencia local

Las siguientes rutas pertenecen al equipo donde se ejecutó la evaluación. Los archivos completos se conservan fuera del repositorio.

- `L:\Novit2026\PIA3-eval\issue9-20261124-045815\resultados` contiene `informe.md`, `resultado.json`, `meta.json`, `martes-tranquilo.json`, `fuga-sin-enlace.json` y los respectivos `.log` del agente.
- `L:\Novit2026\PIA3-eval\issue9-20261124-045815` contiene `corrector.stdout.log`, `corrector.stderr.log`, `corrector-exit.json`, `identidades-observadas.jsonl`, `simulador-durante-corrida.log`, `planta-broker-1-evaluacion.log`, `planta-enlace-1-evaluacion.log` y `restauracion-verificada.json`.

La siguiente prioridad de implementación es el flujo completo de parte por voz y confirmación en tablet. El [plan de Entrega 1](PLAN.md) conserva el estado de cada requisito y sus dependencias.
