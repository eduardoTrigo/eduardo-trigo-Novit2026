# Memoria conversacional del parte por voz

## Responsabilidades y datos

SQLite es la fuente de verdad de las notas, las transiciones del parte, las respuestas, las publicaciones y B1. PostgreSQL ofrece una vista recuperable por ID de nota para los consumidores de memoria. Los workers operativos siguen resolviendo el turno con SQLite y los modelos oficiales.

Cada documento contiene el payload de `NotaRecorrida`, el estado y los intentos de procesamiento de audio, el estado del parte, sus filas vigentes, el contexto de dudas y resoluciones y el contador de interacciones. El historial conserva las versiones conocidas del borrador, el payload de cada `MensajeTablet` retenido en la outbox y las respuestas por ID oficial con su mensaje asociado, resultado, texto transcrito e intentos técnicos de STT. Los contadores técnicos reflejan los trabajos reservados por SQLite, incluidos los fallos previos a la inferencia, como una descarga fallida. Los timestamps de nota, tablet y respuesta provienen de sus payloads. Audio y fotos se referencian por sus rutas originales. Se excluyen los binarios, errores técnicos de texto libre y secretos de configuración.

## Escritura, orden y recuperación

Una fila de cola por nota registra una revisión local creciente en la misma transacción SQLite que inserta o cambia la nota, el parte o una respuesta asociada. La revisión incluye cambios dentro de una misma versión de borrador. SQLite conserva el histórico de versiones y respuestas mientras la cola reúne cambios consecutivos de una nota en una captura consistente. El consumidor prioriza las capturas pendientes más antiguas y libera la transacción de lectura antes de conectar a PostgreSQL.

PostgreSQL acepta por ID de nota solo revisiones superiores. El reconocimiento local ocurre después del commit remoto. Ante una interrupción entre ambos pasos se reenvía la captura de forma idempotente. Un único worker reintenta indefinidamente con espera creciente y acotada, fuera de los callbacks MQTT. La consulta compara la revisión remota con la local y puede indicar que la copia está pendiente o sin referencia local. La comparación refleja el momento de la consulta. Restaurar SQLite y PostgreSQL desde puntos distintos requiere reconciliar revisiones antes de reanudar la sincronización.

Las notas anteriores se incorporan con los datos retenidos en SQLite. Si falta el payload original de tablet, el documento conserva el ID y registra el payload como ausente. Los mensajes desconocidos sin nota asociable permanecen en SQLite. Las fechas de cierre antiguas que SQLite no registró quedan sin determinar.

## Política de memoria y retención

- **Memoria de trabajo.** El worker conserva extracción, aclaración y datos de consulta en RAM durante el paso en curso. El siguiente turno recupera de SQLite el estado que se confirmó allí.
- **Memoria conversacional.** PostgreSQL reúne los turnos por nota. Una consulta explícita o un futuro consumidor recupera el documento por ese ID y considera su estado de sincronización. Las conversaciones activas, incluida la revisión manual, se retienen hasta su resolución.
- **Persistencia operativa.** SQLite mantiene sus propias obligaciones de deduplicación, outbox, recuperaciones y trazabilidad. El ciclo de vida de una proyección PostgreSQL no modifica esos datos.
- **Cierre.** Como decisión provisional, se propone conservar 90 días desde confirmación, rechazo o determinación de que no hay contenido. Solo una fecha de cierre conocida y una revisión sincronizada habilitan considerar un registro para limpieza. Los pendientes de sincronizar y los casos con fecha desconocida se conservan hasta que se resuelvan.
- **Permisos y ejecución.** El rol de aplicación lee, inserta y actualiza la proyección. Una eventual limpieza requerirá autorización, un rol administrativo y un mecanismo que preserve la deduplicación y la política operativa. El borrado automático queda pendiente.

## Aceptación

Un parte creado y corregido por el worker puede confirmarse y recuperarse desde PostgreSQL con su nota, mensajes y respuestas reales, versiones y conteos separados. Una caída de PostgreSQL permite continuar en SQLite y recuperar pendientes después de volver a conectar o reiniciar. Un rollback local no genera una revisión nueva y un reintento posterior al commit remoto conserva la misma revisión. Una base PostgreSQL nueva o inicializada con `001` admite la migración idempotente con permisos mínimos.
