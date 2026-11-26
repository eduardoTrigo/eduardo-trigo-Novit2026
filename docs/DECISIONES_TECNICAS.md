# Decisiones técnicas · Entrega 1

Este registro conserva el motivo y el estado de cada decisión. El seguimiento de pendientes vive en [PLAN.md](PLAN.md).

## DT-01 · Conservar la implementación funcional

- **Estado** · Adoptada.
- **Contexto y justificación** · El [Issue #7](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/7) establece una línea de base documental sobre el circuito de seguridad que ya funciona. Una refactorización mezclaría cambios de comportamiento con el relevamiento.
- **Riesgo** · El sistema existente solo cubre parte de la entrega. La revisión documental no resuelve las brechas del parte, clasificación ni recuperación.
- **Evidencia** · El [entrypoint](../src/sistema/security_integrated.py) conecta el circuito persistente. La auditoría real anterior confirmó arranque y latidos válidos.

## DT-02 · Mantener provisionalmente `faster-whisper base`

- **Estado** · Provisional, sujeto a la lista cerrada de Novit.
- **Contexto y justificación** · El [adaptador actual](../src/sistema/whisper_adapter.py) usa CPU e INT8 y ya se observó la carga del modelo. Conservarlo mantiene una referencia para medir latencia y exactitud.
- **Riesgo** · Quedan pendientes la autorización para la versión y configuración efectivas, la validación sin enlace y la medición bajo el límite de planta. El arranque observado consultó Hugging Face.
- **Evidencia** · [Dependencia declarada](../pyproject.toml), logs de carga de la auditoría anterior y [Anexo oficial citado en el plan](PLAN.md#fuentes-y-línea-de-base).
- **Referencia** · [#7](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/7) y próximo Issue de verificación de eliminatorias.

## DT-03 · Conservar SQLite e integrar PostgreSQL 16 con pgvector

- **Estado** · Infraestructura implementada en el Issue #15. El diseño de memoria e indexación queda para incrementos posteriores.
- **Contexto y justificación** · El Anexo exige que el arranque estándar de Compose incluya PostgreSQL. La imagen oficial de pgvector se fija por etiqueta y digest para reproducir la versión. PostgreSQL valida sus credenciales dentro de su contenedor, de modo que Compose puede iniciar el agente por separado. El rol de aplicación recibe permisos de conexión y el administrador aplica el SQL versionado.
- **Responsabilidades** · SQLite sostiene notas, partes, decisiones B1, evidencia y outbox transaccional. Preservar las claves de deduplicación y la atomicidad entre confirmación, trabajo y publicaciones es parte del contrato interno. PostgreSQL brindará almacenamiento para el estado de conversación por nota y el índice documental. [Transacción SQLite](../src/sistema/security_transaction.py) · [Evaluación B1](EVALUACION_B1.md)
- **Riesgo** · Hay que definir permisos sobre tablas, recuperación y retención para la memoria y el índice. Los dos servicios suman límites de 3 CPU y 3 GiB. Se medirán los picos bajo carga cuando existan consultas e indexación.
- **Evidencia y referencias** · El healthcheck conecta por TCP con el rol de aplicación y verifica la extensión. Las pruebas aisladas comprueban versión, operación vectorial, autenticación y persistencia. [Issue #15](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/15) · [Anexo citado en el plan](PLAN.md#fuentes-y-línea-de-base) · [Procedimiento de verificación](../README.md#pruebas-de-integración-y-persistencia-aisladas)

## DT-04 · Usar GitHub Issues, PLAN.md, Codex y revisión humana

- **Estado** · Adoptada.
- **Contexto y justificación** · GitHub Issues define el trabajo y la aceptación de cada cambio. [PLAN.md](PLAN.md) mantiene requisitos, estado y secuencia. Codex implementa en la rama del Issue y deja evidencia para revisión humana.
- **Riesgo** · Duplicar el seguimiento entre documentos e Issues puede generar estados incompatibles. El plan enlaza los Issues cuando tienen número y referencia sus objetivos sin reproducir sus listas de aceptación.
- **Evidencia y referencia** · [Issue #7](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/7), [plan](PLAN.md) e [instrucciones de trabajo](../AGENTS.md).

## DT-05 · Aplazar la adopción de OpenSpec

- **Estado** · Aplazamiento adoptado. Evaluación de OpenSpec pendiente.
- **Contexto y justificación** · La línea de base usa Markdown y GitHub Issues. Un Issue funcional permitirá evaluar si OpenSpec mejora la trazabilidad y la revisión de cambios frente al flujo actual. La mini spec del parte sigue siendo un requisito de Entrega 1 independiente de la herramienta elegida.
- **Riesgo** · La evaluación puede sumar trabajo o duplicar documentos. Conviene juzgarla por la claridad del cambio y la facilidad de revisar la aceptación.
- **Evidencia y referencia** · [Issue #7](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/7) y requisito de mini spec en [PLAN.md](PLAN.md).
