# Runtime del parte por voz · Issue #29

El Anexo de Entrega 1 exige condiciones de corte, presupuesto de pasos y tokens, timeout global por nota y salida estructurada validada a `FilaParte`. El núcleo del parte usa LangChain en las dos entradas de `WorkerPartes`, una nota transcrita y una respuesta persistida del operador. [Issue #29](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/29)

## Cadena y contratos

Cada entrada usa una `RunnableSequence` real. La primera etapa deriva candidatos y verifica las reglas de área, o interpreta una respuesta con las reglas de aclaración y corrección existentes. La segunda ensambla el contexto de una propuesta o pregunta. La tercera valida el `MensajeTablet` y admite una interfaz opcional de propuestas de extracción. Se reutilizan `extraer_parte`, `verificar_lectura`, `preguntar`, `resolver` e `interpretar_respuesta`. [Cadena del parte](../src/sistema/part_agent.py) · [RunnableLambda de LangChain](https://reference.langchain.com/python/langchain-core/runnables/base/RunnableLambda)

El Context Builder recibe el catálogo del turno y el historian ya consultado al verificar los candidatos. La caché del turno evita lecturas duplicadas. Se reutilizan los modelos y la paginación del catálogo y se preservan los metadatos de un historial incompleto. Las fuentes opcionales de memoria remota y RAG quedan fuera de este flujo. [Mini spec del contexto](SPEC_CONTEXT_BUILDER.md)

Las filas se validan estrictamente con el contrato oficial y se comprueban valores finitos y unicidad de equipo y variable. Una propuesta del adaptador debe coincidir con las filas fundamentadas por la extracción local. El estado, las dudas, el ID, el timestamp y el mensaje se determinan en código. Las fotos permanecen en la nota original y su asociación por fila sigue el alcance de la mini spec del parte. SQLite revalida el turno humano y confirma el parte junto con la evidencia B1. Las cadenas devuelven planes, y `AlmacenPartes` conserva la transacción de borrador y outbox.

## Presupuesto y deadline

| Control | Valor provisional |
| --- | --- |
| Tiempo activo acumulado por nota | 10 s |
| Margen de cierre | 250 ms |
| Pasos por nota | 48, con un paso reservado para cierre |
| Entrada de contexto | 4.200 tokens aproximados por contexto y 20.000 bytes |
| Entrada y salida acumuladas del adaptador | 16.800 y 3.600 tokens presupuestados |
| Reserva de salida por llamada | 900 tokens |
| Reintentos | 1 compartido por nota |
| Interacciones humanas | Límite persistido de 3 del flujo del parte |

`PresupuestoEjecucion` valida tipos y permite configurar los límites al crear el worker. Por turno se fija un deadline monotónico con el tiempo que restaba a la nota. Incluye selección SQLite, reserva, catálogo, historian, extracción, contexto, validaciones, preparación y la transacción final. Se comprueba el tiempo y el número de pasos antes de cada operación. El HTTP recibe el menor plazo entre 2 s y el tiempo disponible para lectura. El historian del parte usa un solo intento por llamada HTTP y el runtime controla su reintento. Una caída recuperable del historian conserva la evidencia de indisponibilidad para las reglas de dominio.

El contador del Context Builder mide una aproximación del JSON. La ruta determinista consume cero tokens de inferencia. Antes de solicitar una propuesta, el runtime descuenta la estimación de entrada y reserva la salida. Si el adaptador informa ambas cantidades reales, ajusta el asiento y comprueba los topes. Los intentos sin medición conservan el cargo provisional. El log distingue los tokens reales de estos cargos y registra costo nulo cuando se desconoce.

Una sola operación aislada por worker puede seguir en vuelo. Si deja de responder, el worker abandona la espera y descarta cualquier resultado tardío. Ese hilo carece de acceso a los métodos de persistencia del parte. Mientras siga ocupado, el runtime evita abrir otro hilo para suplirlo. Las lecturas HTTP también tienen límites propios. SQLite tiene espera por bloqueos acotada, un *progress handler* para la transacción normal y una comprobación previa al commit. El commit nativo puede terminar después de esa comprobación durante un bloqueo de E/S. El cierre seguro tiene un intento acotado de registrar revisión manual, incluso al agotar el tiempo de trabajo.

El límite no equivale a 10 s de latencia total de la recorrida. La cola previa, la descarga, la transcripción inicial y el STT de una respuesta de voz conservan sus propios mecanismos. Algunas dependencias síncronas y el cierre nativo no ofrecen cancelación forzosa. [Código de presupuesto](../src/sistema/execution_budget.py) · [SQLite y progress handler](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.set_progress_handler)

## Recuperación y estados

La tabla aditiva `ejecucion_partes` reserva el turno en SQLite antes de procesarlo. Identifica al dueño y bloquea dos ejecuciones concurrentes de la misma nota. El consumo y resultado se actualizan en el mismo commit del parte, la respuesta y el outbox. Una base previa comienza con sus interacciones operativas y sin consumo histórico medido. Una reversión conocida registra el consumo y permite recuperar la misma tarea con el presupuesto restante. Si vence la reserva de una ejecución interrumpida y su consumo exacto se desconoce, se agota el plazo restante y la nota pasa a revisión manual.

La espera humana queda como `espera_operador` entre turnos. La espera de STT tiene su estado separado. Los duplicados, mensajes desfasados y mensajes desconocidos se clasifican en SQLite y evitan invocar la cadena. Una propuesta válida produce borrador o aclaración pendiente. Los cortes, incluidos pasos, timeout, cancelación y salida inválida sin recuperación, llevan a `revision_manual`. `sin_contenido` conserva la clasificación del dominio.

El fallback reutiliza las filas deterministas solo después de verificar el tiempo y los pasos disponibles. Una salida con acciones o una propuesta sin evidencia se rechaza. El adaptador carece de acceso a herramientas y sus salidas carecen de campos de confirmación o escritura. El Enunciado requiere la lista cerrada de Novit. Los materiales consultados incluyen modelos de demostración y carecen de autorización verificable para el LLM del parte. El despliegue funcionó en modo determinista. La interfaz opcional se ejercitó con dobles de prueba.

## Seguridad y trazas

LangChain se carga en el worker de partes. Seguridad, STT, MQTT, heartbeat, B1 y outbox de alertas conservan sus workers e interfaces. El cliente HTTP del parte tiene su propia caché. La cadena deshabilita el tracing de LangSmith y registra eventos `parte_runtime` con nota, ejecución, etapa, modo, identidad del adaptador, herramientas y argumentos acotados, duración, consumo, reintentos, resultado, motivo y fallback. La traza omite el texto de la nota, prompts y salidas de un modelo. Las instrucciones confiables del Context Builder permanecen separadas de las fuentes externas.

## Ejecución de pruebas

```bash
python -m pytest -q tests/test_agent_runtime.py tests/test_part_workflow.py tests/test_part_clarification.py
```

La prueba temporal habilita S1 mientras una lectura queda bloqueada y confirma que la entrega tardía carece de efecto en el outbox de partes. La prueba de recuperación verifica que un rollback conserva el consumo y que un turno desconocido ya vencido se detiene sin reiniciar el trabajo. Las pruebas de concurrencia verifican una sola reserva y una única publicación.

Para reproducir el borrador aislado en PowerShell, reutiliza la imagen del agente y monta el código de este checkout. El ejemplo crea una nota de prueba y valida el mensaje de outbox. La prueba de la suite `test_cadena_real_persiste_consumo_y_requiere_operador` también envía la confirmación humana y verifica la reanudación.

```powershell
$src = (Resolve-Path src).Path
@'
from datetime import datetime, timezone
from planta_contrato import MensajeTablet, NotaRecorrida
from sistema.note_store import AlmacenNotas
from sistema.part_store import AlmacenPartes
from sistema.part_worker import WorkerPartes
from sistema.security_outbox import OutboxSeguridad
from sistema.security_transaction import completar_con_alertas

notas = AlmacenNotas("/tmp/parte-i29.sqlite3")
OutboxSeguridad(notas.ruta)
bandeja = OutboxSeguridad(notas.ruta, borradores=True)
partes = AlmacenPartes(notas, bandeja)
ts = datetime.now(timezone.utc)
nota = NotaRecorrida(id="NOTA-I29", ts=ts, operador="prueba", audio_url=None, recorrida_id="REC-I29")
catalogo = {"equipos": [{"tag": "B-102", "tipo": "bomba", "nombres": ["la dos"],
                        "manometro_local": {"unidad": "psi"}}], "sensores": []}
worker = WorkerPartes(partes, lambda: catalogo)
assert notas.guardar(nota) and notas.reclamar_siguiente() == nota
assert completar_con_alertas(notas, nota.id, [], None, transcripcion="La dos en 1640 de descarga")
assert worker.procesar_siguiente()
mensaje = MensajeTablet.model_validate_json(bandeja.reclamar()["payload"], strict=True)
assert mensaje.ts == ts and mensaje.espera_respuesta and mensaje.pantalla.filas[0].valor == 1640
assert partes.obtener(nota.id).estado == "borrador_pendiente"
print(partes.estado_ejecucion(nota.id).consumo.model_dump_json())
'@ | docker run -i --pull never --network none --read-only --tmpfs /tmp:rw,nosuid,nodev,size=32m `
  --cpus 0.5 --memory 512m --mount "type=bind,source=$src,target=/verificacion/src,readonly" `
  --env PYTHONPATH=/verificacion/src eduardo-trigo-agente-planta:latest python -
```

La comprobación Docker reutilizó la imagen habitual con `src/` montado en solo lectura, `--network none` y SQLite en tmpfs. Una nota de prueba produjo un borrador válido en el outbox. Una respuesta humana de prueba cerró el parte. La cadena fue `RunnableSequence` y el consumo registrado fue de 9 pasos, 493 ms activos, 571 tokens aproximados de contexto, cero tokens de inferencia y una interacción. Docker usó LangChain 1.5.2 y langchain-core 1.8.5. La suite local usó LangChain 1.5.2 y langchain-core 1.8.4. El contenedor de prueba tuvo límites de 0,5 CPU y 512 MiB. [Pruebas del runtime](../tests/test_agent_runtime.py)

La suite habitual con el corpus oficial dio 577 aprobados y 19 omitidos, incluidas 18 pruebas nuevas. PostgreSQL y memoria dieron 6 aprobados en la instancia aislada. Catálogo y MCP dieron 6 aprobados con el simulador y la imagen existentes. También pasaron `docker compose config --quiet` y `git diff --check`.
