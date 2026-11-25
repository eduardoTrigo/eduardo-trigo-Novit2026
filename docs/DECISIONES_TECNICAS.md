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

- **Estado** · Adoptada la conservación temporal de SQLite. PostgreSQL 16 con pgvector y memoria de conversación será la próxima prioridad tras cerrar B1.
- **Contexto y justificación** · SQLite sostiene la persistencia de notas y la outbox transaccional. El Anexo exige Postgres dentro de Compose, memoria de la conversación y el índice vectorial. Se incorporarán esas capacidades sin reemplazar por anticipado la persistencia de seguridad.
- **Riesgo** · Dos almacenes requieren definir qué datos pertenecen a cada uno, cómo se recuperan y qué presupuesto de recursos consumen. Cualquier migración del circuito actual requiere su propia validación.
- **Evidencia** · La corrida del Issue #13 registró decisiones y envíos B1 en SQLite. Su traslado requiere preservar las claves de deduplicación y las transacciones que agrupan confirmación, trabajo, evidencia y outbox. [Evaluación B1](EVALUACION_B1.md) · [Transacción SQLite](../src/sistema/security_transaction.py) · [Anexo citado en el plan](PLAN.md#fuentes-y-línea-de-base)
- **Referencia** · [#7](https://github.com/eduardoTrigo/eduardo-trigo-Novit2026/issues/7) y futuro Issue de parte por voz y memoria.

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
