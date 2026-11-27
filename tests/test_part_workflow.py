import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from planta_contrato import MensajeTablet, RespuestaOperador

from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_security_messages import crear_nota


def preparar_borrador(tmp_path, texto="La uno está en 110 kilos de descarga"):
    notas, seguridad, borradores, partes, worker = preparar(tmp_path)
    nota = crear_nota()
    transcribir_nota(notas, nota, texto)
    assert worker.procesar_siguiente()
    return nota, seguridad, borradores, partes, worker


def respuesta(parte, texto, *, identificador="RESP-001", audio=None):
    return RespuestaOperador(
        id=identificador,
        mensaje_id=parte.mensaje_id,
        ts=parte.nota.ts,
        operador=parte.nota.operador,
        texto=texto,
        audio_url=audio,
    )


def procesar(partes, worker, mensaje):
    assert partes.guardar_respuesta(mensaje)
    assert worker.procesar_siguiente()


def resultado(partes, identificador):
    with sqlite3.connect(partes.ruta) as db:
        return db.execute(
            "SELECT resultado FROM respuestas_parte WHERE id = ?", (identificador,)
        ).fetchone()[0]


@pytest.mark.parametrize("texto", ["Sí, confirmo.", "Está bien, confirmo.", "Correcto, dale.",
                                  "Sí, así es. Confirmo.", "Perfecto, confirmo."])
def test_confirma_borrador_vigente(tmp_path, texto):
    nota, seguridad, borradores, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, texto)
    procesar(partes, worker, r)
    final = partes.obtener(nota.id)
    assert final.estado == "confirmado" and final.version == 1
    assert final.filas == inicial.filas
    assert resultado(partes, r.id) == "confirmada"
    assert borradores.pendientes() == 1 and seguridad.pendientes() == 0


@pytest.mark.parametrize(
    "texto_nota,texto_respuesta,esperado",
    [
        ("La uno está en 110 kilos de descarga",
         "No, la uno estaba en ciento quince kilos.", ("B-101", 115, "kg/cm2")),
        ("La dos está en 115 kilos de descarga",
         "No. Eso de ciento quince era de la uno, no de la dos. Arreglá eso.",
         ("B-101", 115, "kg/cm2")),
        ("La dos está en 1640 kilos de descarga",
         "No. Lo de la dos es en libras, no en kilos. Corregilo y te confirmo.",
         ("B-102", 1640, "psi")),
        ("La uno está en 110 kilos de descarga",
         "No. La uno estaba en ciento quince kilos, no en ciento diez. Con eso está.",
         ("B-101", 115, "kg/cm2")),
    ],
)
def test_correccion_reenvia_version_y_requiere_confirmacion(tmp_path, texto_nota, texto_respuesta, esperado):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path, texto_nota)
    inicial = partes.obtener(nota.id)
    cambio = respuesta(inicial, texto_respuesta)
    procesar(partes, worker, cambio)
    nuevo = partes.obtener(nota.id)
    assert nuevo.estado == "borrador_pendiente" and nuevo.version == 2
    assert nuevo.mensaje_id == f"PARTE-{nota.id}-2"
    fila = nuevo.filas[0]
    assert (fila.tag, fila.valor, fila.unidad) == esperado
    assert resultado(partes, cambio.id) == "corregida"
    assert borradores.reclamar()["id"] == inicial.mensaje_id
    payload = MensajeTablet.model_validate_json(borradores.reclamar()["payload"])
    assert payload.id == nuevo.mensaje_id and payload.pantalla.filas == [fila]
    assert payload.espera_respuesta and payload.nota_ids == [nota.id]

    vieja = respuesta(inicial, "Sí, confirmo.", identificador="RESP-ANTIGUA")
    procesar(partes, worker, vieja)
    assert resultado(partes, vieja.id) == "desfasada"
    assert partes.obtener(nota.id) == nuevo
    actual = respuesta(nuevo, vieja.texto, identificador="RESP-NUEVA")
    procesar(partes, worker, actual)
    assert partes.obtener(nota.id).estado == "confirmado"


@pytest.mark.parametrize("texto,audio,esperado", [
    ("Rechazo el parte.", None, "rechazada"),
    ("No lo guardes.", None, "rechazada"),
    ("No, no confirmo.", None, "insuficiente"),
    ("No sé si era ciento diez o quince.", None, "insuficiente"),
    ("", "/media/audios/RESP-001.wav", "requiere_stt"),
])
def test_rechazo_o_respuesta_insuficiente(tmp_path, texto, audio, esperado):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, texto, audio=audio)
    procesar(partes, worker, r)
    final = partes.obtener(nota.id)
    assert resultado(partes, r.id) == esperado
    assert final.estado == ("rechazado" if esperado == "rechazada" else "borrador_pendiente")
    assert final.version == 1 and final.filas == inicial.filas
    assert borradores.pendientes() == 1


def test_id_duplicado_desfasado_y_mensaje_desconocido(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, "No, la uno estaba en ciento quince kilos.")
    procesar(partes, worker, r)
    assert not partes.guardar_respuesta(r)
    assert not worker.procesar_siguiente()
    previo = partes.obtener(nota.id)

    repetida = r.model_copy(update={"id": "RESP-OTRA"})
    procesar(partes, worker, repetida)
    assert resultado(partes, repetida.id) == "desfasada"
    desconocida = r.model_copy(update={"id": "RESP-X", "mensaje_id": "TABLET-SEG-NOTA-X"})
    procesar(partes, worker, desconocida)
    assert resultado(partes, desconocida.id) == "mensaje_desconocido"
    assert partes.obtener(nota.id) == previo
    assert borradores.pendientes() == 2


def test_recupera_respuesta_y_corrige_atomicamente_despues_de_rollback(tmp_path):
    nota, _, borradores, partes, _ = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, "No, la uno estaba en ciento quince kilos.")
    assert partes.guardar_respuesta(r)
    with sqlite3.connect(partes.ruta) as db:
        db.execute("""
            CREATE TRIGGER fallar_correccion BEFORE INSERT ON outbox
            BEGIN SELECT RAISE(ABORT, 'fallo de prueba'); END
        """)
    _, _, _, reabierto, worker = preparar(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert reabierto.obtener(nota.id) == inicial
    revertida = reabierto.estado_ejecucion(nota.id)
    assert revertida.motivo == "persistencia_revertida"
    assert resultado(reabierto, r.id) is None
    assert borradores.pendientes() == 1
    with sqlite3.connect(reabierto.ruta) as db:
        assert db.execute("SELECT COUNT(*) FROM borradores_parte").fetchone() == (1,)
        db.execute("DROP TRIGGER fallar_correccion")
    assert worker.procesar_siguiente()
    assert reabierto.estado_ejecucion(nota.id).consumo.pasos > revertida.consumo.pasos
    assert resultado(reabierto, r.id) == "corregida"
    assert reabierto.obtener(nota.id).version == 2 and borradores.pendientes() == 2


def test_corrige_hasta_tercera_interaccion(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    for i, valor in enumerate((115, 116, 117), start=1):
        r = respuesta(partes.obtener(nota.id), f"No, la uno estaba en {valor} kilos.",
                      identificador=f"RESP-{i}")
        procesar(partes, worker, r)
    parte = partes.obtener(nota.id)
    assert parte.estado == "revision_manual" and parte.version == 3
    assert parte.filas[0].valor == 116
    assert resultado(partes, "RESP-3") == "limite_interacciones"
    assert borradores.pendientes() == 3


def test_tres_respuestas_insuficientes_distintas_agotan_turno_sin_cambiar_version(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    primera = respuesta(inicial, "No sé.")
    procesar(partes, worker, primera)
    assert not partes.guardar_respuesta(primera)
    repetida = primera.model_copy(update={"id": "DUP", "texto": "NO SÉ."})
    procesar(partes, worker, repetida)
    assert resultado(partes, repetida.id) == "duplicada"

    _, _, _, partes, worker = preparar(tmp_path)
    assert partes.obtener(nota.id).interacciones == 1
    segunda = respuesta(inicial, "No recuerdo.", identificador="RESP-SEGUNDA")
    procesar(partes, worker, segunda)
    assert partes.obtener(nota.id).estado == "borrador_pendiente"
    tercera = respuesta(inicial, "No entendí.", identificador="RESP-TERCERA")
    procesar(partes, worker, tercera)
    final = partes.obtener(nota.id)
    assert final.estado == "revision_manual" and final.interacciones == 3 and final.version == 1
    assert final.mensaje_id == inicial.mensaje_id and final.filas == inicial.filas
    assert resultado(partes, tercera.id) == "limite_interacciones"
    assert borradores.pendientes() == 1


def test_migracion_cuenta_insuficientes_historicas_y_turnos_anteriores(tmp_path):
    nota, _, _, partes, worker = preparar_borrador(tmp_path)
    r1 = respuesta(partes.obtener(nota.id), "No. La uno estaba en ciento quince kilos.")
    procesar(partes, worker, r1)
    vigente = partes.obtener(nota.id)
    r2 = respuesta(vigente, "No recuerdo.", identificador="RESP-SEGUNDA")
    procesar(partes, worker, r2)
    with sqlite3.connect(partes.ruta) as db:
        columnas = {r[1] for r in db.execute("PRAGMA table_info(partes)")}
        if "interacciones" in columnas:
            db.execute("ALTER TABLE partes DROP COLUMN interacciones")

    _, _, _, partes, worker = preparar(tmp_path)
    vigente = partes.obtener(nota.id)
    assert vigente.version == 2 and vigente.interacciones == 2
    r3 = respuesta(vigente, "Sí, confirmo.", identificador="RESP-TERCERA")
    procesar(partes, worker, r3)
    final = preparar(tmp_path)[3].obtener(nota.id)
    assert final.estado == "confirmado" and final.interacciones == 3
    assert resultado(partes, r2.id) == "insuficiente"


def test_si_se_omite_actualizacion_se_revierte_borrador_y_respuesta(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    inicial = partes.obtener(nota.id)
    r = respuesta(inicial, "No, la uno estaba en ciento quince kilos.")
    assert partes.guardar_respuesta(r)
    with sqlite3.connect(partes.ruta) as db:
        db.execute("""
            CREATE TRIGGER omitir_correccion BEFORE UPDATE ON partes
            BEGIN SELECT RAISE(IGNORE); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        worker.procesar_siguiente()
    assert partes.obtener(nota.id) == inicial
    assert resultado(partes, r.id) is None and borradores.pendientes() == 1


def test_dos_lectores_de_la_misma_respuesta_aplican_una_sola_version(tmp_path):
    nota, _, borradores, partes, worker = preparar_borrador(tmp_path)
    r = respuesta(partes.obtener(nota.id), "No, la uno estaba en ciento quince kilos.")
    assert partes.guardar_respuesta(r)
    en_catalogo, liberar = Event(), Event()

    def catalogo():
        en_catalogo.set()
        assert liberar.wait(timeout=5)
        return definicion_planta()

    worker.catalogo = catalogo
    with ThreadPoolExecutor(max_workers=2) as pool:
        primero = pool.submit(worker.procesar_siguiente)
        try:
            assert en_catalogo.wait(timeout=3)
            assert pool.submit(worker.procesar_siguiente).result(timeout=3) is False
        finally:
            liberar.set()
        assert primero.result(timeout=3) is True
    assert partes.obtener(nota.id).version == 2
    assert resultado(partes, r.id) == "corregida" and borradores.pendientes() == 2


def test_una_correccion_esperando_catalogo_no_bloquea_seguridad(tmp_path):
    nota, seguridad, _, partes, worker = preparar_borrador(tmp_path)
    r = respuesta(partes.obtener(nota.id), "No, la uno estaba en ciento quince kilos.")
    assert partes.guardar_respuesta(r)
    desde_catalogo = Event()
    liberar = Event()

    def catalogo():
        desde_catalogo.set()
        assert liberar.wait(timeout=5)
        return definicion_planta()

    worker.catalogo = catalogo
    with ThreadPoolExecutor(max_workers=1) as pool:
        pendiente = pool.submit(worker.procesar_siguiente)
        try:
            assert desde_catalogo.wait(timeout=5)
            from sistema.note_store import AlmacenNotas

            insegura = nota.model_copy(update={"id": "NOTA-SEG"})
            transcribir_nota(AlmacenNotas(partes.ruta), insegura, "Sale humo de la bomba")
            assert seguridad.reclamar()["id"] == f"SEG-{insegura.id}-S3"
            assert seguridad.reclamar()["id"] == f"TABLET-SEG-{insegura.id}"
        finally:
            liberar.set()
        assert pendiente.result(timeout=10)


def test_migracion_recupera_correlacion_del_borrador_previo(tmp_path):
    from sistema.note_store import AlmacenNotas
    from sistema.part_messages import crear_borrador
    from sistema.part_store import AlmacenPartes
    from sistema.part_worker import WorkerPartes
    from sistema.security_outbox import OutboxSeguridad
    from planta_contrato import FilaParte

    notas = AlmacenNotas(tmp_path / "seguridad.sqlite3")
    OutboxSeguridad(notas.ruta)
    borradores = OutboxSeguridad(notas.ruta, borradores=True)
    nota = crear_nota()
    transcribir_nota(notas, nota, "La uno está en 110 kilos de descarga")
    fila = FilaParte(tag="B-101", variable="presion_descarga_local", valor=110, unidad="kg/cm2")
    mensaje = crear_borrador(nota, [fila])
    with sqlite3.connect(notas.ruta) as db:
        db.execute("""
            CREATE TABLE partes (
                nota_id TEXT PRIMARY KEY REFERENCES notas(id),
                estado TEXT NOT NULL, filas TEXT NOT NULL, mensaje_id TEXT UNIQUE
            )
        """)
        db.execute("INSERT INTO partes VALUES (?, 'borrador_pendiente', ?, ?)",
                   (nota.id, f"[{fila.model_dump_json()}]", mensaje.id))
    assert borradores.guardar(mensaje.id, "sistema/tablet", mensaje.model_dump_json())

    AlmacenPartes(notas, borradores)
    partes = AlmacenPartes(notas, borradores)
    r = respuesta(partes.obtener(nota.id), "Sí, confirmo.")
    procesar(partes, WorkerPartes(partes, definicion_planta), r)
    confirmado = partes.obtener(nota.id)
    assert confirmado.estado == "confirmado" and confirmado.version == 1
    assert confirmado.filas == [fila] and borradores.pendientes() == 1
