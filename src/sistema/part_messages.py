"""Borradores de tablet conformes al contrato oficial."""

from planta_contrato import FilaParte, MensajeTablet, NotaRecorrida, Pantalla


def crear_borrador(nota: NotaRecorrida, filas: list[FilaParte], *, version: int = 1) -> MensajeTablet:
    return MensajeTablet(
        id=f"PARTE-{nota.id}-{version}",
        ts=nota.ts,
        nota_ids=[nota.id],
        texto="Este es el borrador del parte de recorrida. ¿Confirmás las lecturas?",
        pantalla=Pantalla(titulo="Parte de recorrida · borrador", filas=filas),
        espera_respuesta=True,
    )


def crear_aclaracion(
    nota: NotaRecorrida, texto: str, *, version: int,
    opciones: list[str], filas: list[FilaParte],
) -> MensajeTablet:
    return MensajeTablet(
        id=f"PARTE-{nota.id}-{version}", ts=nota.ts, nota_ids=[nota.id], texto=texto,
        pantalla=Pantalla(titulo="Parte de recorrida · aclaración", opciones=opciones, filas=filas),
        espera_respuesta=True,
    )
