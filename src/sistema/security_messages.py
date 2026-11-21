
"""Mensajes de seguridad según el contrato oficial PIA-3."""

from planta_contrato import (
    EventoSistema,
    MensajeTablet,
    NotaRecorrida,
    TipoEvento,
)

from sistema.security_detector import CondicionInsegura


def crear_mensajes_seguridad(
    nota: NotaRecorrida,
    condiciones: list[CondicionInsegura],
) -> tuple[list[EventoSistema], MensajeTablet | None]:
    """Construye eventos y un aviso para la tablet."""

    if not condiciones:
        return [], None

    eventos = []

    for condicion in condiciones:
        evento = EventoSistema(
            id=f"SEG-{nota.id}-{condicion.codigo}",
            ts=nota.ts,
            tipo=TipoEvento.alerta_seguridad,
            origen="parte",
            nota_ids=[nota.id],
            resumen=(
                f"{condicion.codigo}: {condicion.descripcion}. "
                "Condición insegura detectada."
            ),
        )
        eventos.append(evento)

    descripciones = ", ".join(
        condicion.descripcion
        for condicion in condiciones
    )

    tablet = MensajeTablet(
        id=f"TABLET-SEG-{nota.id}",
        ts=nota.ts,
        nota_ids=[nota.id],
        texto=(
            f"ALERTA DE SEGURIDAD: {descripciones}. "
            "Aplique el procedimiento de emergencia "
            "y avise por radio al supervisor de turno."
        ),
        espera_respuesta=False,
    )

    return eventos, tablet
