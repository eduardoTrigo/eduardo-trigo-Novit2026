# Evaluación oficial del Issue #11 · PIA-3

El 24 de noviembre de 2026, el corrector ejecutó `martes-tranquilo` y `fuga-sin-enlace` y terminó con código `0`. La corrida obtuvo **67,3/100**, una mejora de **28,4 puntos** frente a los **38,9/100** de la [línea de base del Issue #9](EVALUACION_ENTREGA1.md). El mínimo de aprobación era 60. El corrector reescaló la nota tomando los criterios medidos, cuyo peso sumó 63 de los 93 puntos de la rúbrica. Se superaron las eliminatorias `sin_alerta_de_seguridad` y `sistema_mudo`.

## Versión evaluada y corrección posterior

Se evaluó una copia de la rama `11-feat-voice-parts` con los cambios locales de ese momento sobre HEAD `b0d7f353cc7cd54536ff29648c1973f04517ea6d`. Ambos escenarios usaron la imagen `sha256:307c5fe905c806ffb5ca96120fb5084352bf0665230e23e26268f754767d71a0`. Los archivos `workspace.json` e `imagen-verificada.json` registran la huella del código y la identidad de la imagen.

**Los 67,3/100 corresponden a la corrida anterior a la última corrección del estado de aclaración.** La versión corregida deriva a revisión manual los partes que todavía tienen contenido sin interpretar y carecen de una pregunta concreta. También recupera los registros anteriores que habían quedado esperando sin un turno vigente. La suite posterior reportó **187 pruebas aprobadas y 0 fallidas**. Esa versión aún no fue reevaluada con el corrector oficial. [Mini especificación del parte por voz](SPEC_PARTE_VOZ.md)

## Puntajes oficiales

| Criterio | Issue #9 | Issue #11 |
| --- | ---: | ---: |
| A1 · Cobertura de borradores | 0/5 | 3,19/5 |
| A2 · Confirmación al primer borrador | 0/10 | 5,83/10 |
| A3 · Parte final correcto | 0/8 | 5,11/8 |
| A4a · Preguntas ante ambigüedad | 0/4 | 4/4 |
| A4b · Evitar preguntas adicionales | 3/3 | 2,79/3 |
| B2 · Alerta de seguridad | 10/10 | 10/10 |
| F1 · Validez de mensajes | 2/2 | 2/2 |
| F2 · Heartbeat | 1/1 | 1/1 |

## Partes, tiempos y seguridad

| Escenario | Notas | Notas con borrador | Partes confirmados en SQLite | Publicaciones válidas | Latidos |
| --- | ---: | ---: | ---: | ---: | ---: |
| `martes-tranquilo` | 9 | 7 | 7 | 20 | 9 |
| `fuga-sin-enlace` | 4 | 2 | 2 | 10 | 5 |
| **Total** | **13** | **9** | **9** | **30** | **14** |

- El corrector registró ocho confirmaciones al primer borrador, preguntas en las tres notas con ambigüedad de equipo y una pregunta adicional en la nota clara `NOTA-00006` de `martes-tranquilo`. Las capturas SQLite registraron tres aclaraciones resueltas, una corrección aplicada y una respuesta `requiere_stt`.
- El tiempo desde la emisión de la nota hasta el primer borrador fue de **2,52–11,61 s**. Los 12 tiempos activos medidos por MQTT, desde la recepción hasta el primer envío y desde cada respuesta hasta el siguiente envío, fueron de **2,45–4,23 s** por nota. La espera del operador se midió por separado y llegó a **12,59 s**. La duración del guardado final en SQLite queda pendiente de medir.
- En `fuga-sin-enlace`, la nota `NOTA-00001` produjo la alerta S1 y el aviso de tablet durante el corte en **2,5307 s** y **2,5308 s**, respectivamente. En ambos escenarios se registraron cero falsas alarmas, cero publicaciones inválidas y cero duplicados lógicos.
- Los intervalos entre latidos fueron de **29,55–30,46 s**. Durante la ventana del corte se observaron cero latidos y los borradores se publicaron con el enlace restablecido.

## Restauración verificada

Los seis contenedores de desarrollo conservaron sus IDs, imágenes y montajes. Se verificaron los seis respaldos TAR, incluidos los volúmenes anónimos de Mosquitto, y el SHA-256 de las grabaciones. El respaldo en frío de SQLite tenía **116 notas y 0 mensajes en outbox**. La comprobación posterior encontró las 116 notas preservadas entre **121 notas totales**, con 0 mensajes en outbox y `integrity_check` en `ok`. El escenario `libre` conservó su progreso y recibió un heartbeat reciente.

## Limitaciones y pendientes

- La evidencia oficial se limita a los dos escenarios ejecutados y a la imagen anterior a la corrección. Falta reevaluar esa corrección, ampliar la evidencia de S2–S4 en escenarios oficiales y medir el guardado final para completar la evaluación del presupuesto de 10 s.
- Revisar las cuatro notas sin borrador, la pregunta adicional de A4b y el caso `requiere_stt`. B1 quedó en 0/10. El [plan de Entrega 1](PLAN.md) mantiene el seguimiento de esos requisitos.
- OFF-1 sigue parcialmente validado. Quedan pendientes el flujo completo de partes durante el corte y la verificación del estado efectivo del enlace y de los pendientes en el heartbeat.
- `faster-whisper base` cargó sin red con la caché respaldada. Quedan pendientes la autorización oficial del modelo y el arranque sin Internet con caché vacía.

## Evidencia local

Las siguientes son **rutas locales de Windows, sin enlaces públicos**. Los nombres se resuelven bajo `L:\Novit2026\PIA3-eval\issue11-20261124-085637`.

| Evidencia | Archivo relativo a la carpeta local |
| --- | --- |
| Informe consolidado y ejecución del corrector | `INFORME_ISSUE11.md`, `corrector-exit.json` |
| Rúbrica aplicada, puntajes y escenarios | `simulador/correccion/rubrica.yaml`, `resultados/informe.md`, `resultados/resultado.json`, `resultados/meta.json`, `resultados/martes-tranquilo.json`, `resultados/fuga-sin-enlace.json` |
| Tiempos, capturas MQTT y partes confirmados | `metricas-verificadas.json`, `pia3-i11-mqtt-20261124.jsonl`, `sqlite-martes-tranquilo.json`, `sqlite-fuga-sin-enlace.json` |
| Código, imagen evaluada y arranque sin red | `workspace.json`, `imagen-verificada.json`, `prueba-imagen-whisper-offline.log` |
| Respaldo y restauración | `respaldo/verificacion.json`, `restauracion-verificada.json` |
| Suite previa a la corrección, 183 pruebas | `pia3-i11-pytest.log` |
