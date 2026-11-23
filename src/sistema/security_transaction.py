
"""Transacción atómica entre notas y alertas PIA-3."""

import sqlite3

from planta_contrato import EventoSistema, MensajeTablet, Topicos

from sistema.note_store import AlmacenNotas


def completar_con_alertas(
    almacen: AlmacenNotas,
    nota_id: str,
    eventos: list[EventoSistema],
    tablet: MensajeTablet | None,
) -> bool:
    """Guarda alertas y completa la nota en una transacción.

    Requiere que notas y outbox estén en el mismo archivo SQLite.
    """

    if eventos and tablet is None:
        raise ValueError("Una alerta requiere aviso de tablet")

    if tablet is not None and not eventos:
        raise ValueError("Aviso de tablet sin eventos")

    publicaciones = [
        (evento.id, Topicos.EVENTOS, evento.model_dump_json())
        for evento in eventos
    ]

    if tablet is not None:
        publicaciones.append(
            (
                tablet.id,
                Topicos.TABLET,
                tablet.model_dump_json(),
            )
        )

    with sqlite3.connect(almacen.ruta, timeout=5) as db:
        db.execute("BEGIN IMMEDIATE")

        fila = db.execute(
            "SELECT estado FROM notas WHERE id = ?",
            (nota_id,),
        ).fetchone()

        if fila is None or fila[0] != "procesando":
            return False

        for identificador, topico, payload in publicaciones:
            existente = db.execute(
                """
                SELECT topico, payload
                FROM outbox
                WHERE id = ?
                """,
                (identificador,),
            ).fetchone()

            if existente is not None:
                if existente != (topico, payload):
                    raise ValueError(
                        f"Conflicto de contenido: {identificador}"
                    )
                continue

            db.execute(
                """
                INSERT INTO outbox (id, topico, payload)
                VALUES (?, ?, ?)
                """,
                (identificador, topico, payload),
            )

        cursor = db.execute(
            """
            UPDATE notas
            SET estado = 'completada',
                ultimo_error = NULL,
                actualizado = CURRENT_TIMESTAMP
            WHERE id = ? AND estado = 'procesando'
            """,
            (nota_id,),
        )

        return cursor.rowcount == 1
