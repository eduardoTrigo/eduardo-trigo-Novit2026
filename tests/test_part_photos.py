import sqlite3
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest

from sistema.part_context import ClientePlanta, MAX_BYTES_FOTO_B1
from sistema.execution_budget import PresupuestoEjecucion
from sistema.part_memory_store import AlmacenMemoriaLocal
from sistema.part_photos import capturar_fotos, reconsiderar_fotos
from sistema.part_worker import WorkerPartes
from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import procesar, respuesta, resultado
from test_security_messages import crear_nota


FOTO = "/media/fotos/prueba.jpg"
JPEG = b"\xff\xd8\xffprueba\xff\xd9"
NOTA_CON_FOTO = ("La uno 112 kilos de descarga. La dos gotea por la empaquetadura. "
                 "Saqué foto de la empaquetadura de la dos.")
NOTA_UNIDAD = "La dos 1640 pc de descarga. Saqué foto del manómetro de descarga de la dos."


@pytest.mark.parametrize(("base", "ruta"), [
    ("http://externo:8100", FOTO), ("http://sim@externo:8100", FOTO),
    ("http://sim:8100", "http://externo/media/fotos/prueba.jpg"),
    ("http://sim:8100", "/media/fotos/../prueba.jpg"),
    ("http://sim:8100", "/media/fotos/%2e%2e/prueba.jpg"),
])
def test_origen_y_rutas_se_rechazan_antes_de_la_red(monkeypatch, base, ruta):
    red = MagicMock()
    monkeypatch.setattr(httpx, "stream", red)
    with pytest.raises(ValueError):
        ClientePlanta(base).foto_parte(ruta, plazo=0.5)
    red.assert_not_called()


@pytest.mark.parametrize(("estado", "mime", "contenido", "longitud", "error"), [
    (200, "image/jpeg", JPEG, len(JPEG), None),
    (200, "text/plain", JPEG, len(JPEG), ValueError),
    (200, "image/jpeg", b"texto", None, ValueError),
    (200, "image/jpeg", JPEG[:-2], None, ValueError),
    (200, "image/jpeg", JPEG, len(JPEG) + 1, ValueError),
    (200, "image/jpeg", JPEG, MAX_BYTES_FOTO_B1 + 1, ValueError),
    (200, "image/jpeg", JPEG * MAX_BYTES_FOTO_B1, None, ValueError),
    (302, "image/jpeg", JPEG, None, httpx.HTTPStatusError),
    (404, "image/jpeg", JPEG, None, httpx.HTTPStatusError),
])
def test_descarga_compartida_valida_formato_integridad_y_tamano(
    monkeypatch, estado, mime, contenido, longitud, error,
):
    @contextmanager
    def stream(metodo, url, *, timeout, follow_redirects, headers, trust_env):
        assert metodo == "GET" and url == "http://sim:8100" + FOTO
        assert timeout == 0.5 and not follow_redirects and not trust_env
        assert headers == {"Accept-Encoding": "identity"}
        cabeceras = {"content-type": mime, "location": "http://externo"}
        if longitud is not None:
            cabeceras["content-length"] = str(longitud)
        yield httpx.Response(estado, headers=cabeceras, content=contenido,
                             request=httpx.Request(metodo, url))

    monkeypatch.setattr(httpx, "stream", stream)
    cliente = ClientePlanta("http://sim:8100")
    if error:
        with pytest.raises(error):
            cliente.foto_parte(FOTO, plazo=0.5)
    else:
        assert cliente.foto_parte(FOTO, plazo=0.5) == ("image/jpeg", JPEG)


def iniciar(tmp_path, texto, fotos, descargar, **kwargs):
    notas, seguridad, borradores, partes, _ = preparar(tmp_path)
    nota = crear_nota().model_copy(update={"fotos": fotos})
    transcribir_nota(notas, nota, texto)
    worker = WorkerPartes(partes, definicion_planta, foto=descargar, **kwargs)
    assert worker.procesar_siguiente()
    return nota, seguridad, borradores, partes, worker


def test_foto_asociada_persiste_por_hash_y_se_proyecta_sin_binario(tmp_path):
    descargar = MagicMock(return_value=("image/jpeg", JPEG))
    nota, _, _, partes, worker = iniciar(tmp_path, NOTA_CON_FOTO, [FOTO, FOTO], descargar)
    parte = partes.obtener(nota.id)
    evidencia, = parte.contexto["fotos"]
    assert evidencia["nota_id"] == nota.id and evidencia["ruta"] == FOTO
    assert (evidencia["tag"], evidencia["variable"]) == ("B-102", "goteo_empaquetadura")
    assert evidencia["estado_descarga"] == "descargada" and evidencia["estado_asociacion"] == "fila"
    assert evidencia["evidencia"]
    procesar(partes, worker, respuesta(parte, "Confirmo"))
    _, _, _, reabiertas, worker_nuevo = preparar(tmp_path)
    persistido = reabiertas.obtener(nota.id)
    assert persistido.estado == "confirmado" and persistido.contexto["fotos"] == [evidencia]
    assert reabiertas.obtener_foto(evidencia["sha256"]) == ("image/jpeg", JPEG)
    assert not worker_nuevo.procesar_siguiente()
    descargar.assert_called_once()
    captura = AlmacenMemoriaLocal(partes.ruta).siguiente().documento
    assert captura["parte"]["contexto"]["fotos"] == captura["borradores"][0]["contexto"]["fotos"] == [evidencia]
    with sqlite3.connect(partes.ruta) as db:
        assert db.execute("SELECT count(*) FROM fotos_partes_blob").fetchone() == (1,)


def test_varias_fotos_sin_correspondencia_quedan_en_la_nota_y_deduplican_bytes(tmp_path):
    foto2 = "/media/fotos/otra.jpg"
    nota, _, _, partes, _ = iniciar(tmp_path, NOTA_CON_FOTO, [FOTO, foto2, "/invalida"],
                                    lambda ruta, *, plazo: ("image/jpeg", JPEG))
    fotos = partes.obtener(nota.id).contexto["fotos"]
    assert [f["ruta"] for f in fotos] == nota.fotos
    assert all(f["estado_asociacion"] == "nota" and f["tag"] is None for f in fotos)
    assert fotos[0]["sha256"] == fotos[1]["sha256"] and fotos[2]["estado_descarga"] == "invalida"
    with sqlite3.connect(partes.ruta) as db:
        assert db.execute("SELECT count(*) FROM fotos_partes_blob").fetchone() == (1,)


def test_foto_lenta_no_impide_borrador_ni_pierde_la_referencia(tmp_path):
    def lenta(ruta, *, plazo):
        time.sleep(plazo + 1)
        return "image/jpeg", JPEG

    nota, seguridad, borradores, partes, _ = iniciar(
        tmp_path, "La uno 112 kilos de descarga.", [FOTO], lenta,
    )
    parte = partes.obtener(nota.id)
    assert parte.estado == "borrador_pendiente" and borradores.pendientes() == 1
    assert seguridad.pendientes() == 0
    assert parte.contexto["fotos"][0]["estado_descarga"] == "timeout"
    assert parte.contexto["fotos"][0]["ruta"] == FOTO


def test_rectificacion_de_equipo_revalida_asociacion_sin_reasignar_la_foto(tmp_path):
    texto = ("La dos 1640 psi de descarga. La uno gotea por la empaquetadura. "
             "Saqué foto del manómetro de descarga de la dos.")
    nota, _, _, partes, worker = iniciar(tmp_path, texto, [FOTO], lambda ruta, *, plazo: ("image/jpeg", JPEG))
    inicial = partes.obtener(nota.id)
    assert inicial.contexto["fotos"][0]["estado_asociacion"] == "fila"
    procesar(partes, worker, respuesta(inicial, "Eso de 1640 era de la tres, no de la dos."))
    corregido = partes.obtener(nota.id)
    assert corregido.version == 2 and {f.tag for f in corregido.filas} == {"B-101", "B-103"}
    anterior, nueva = inicial.contexto["fotos"][0], corregido.contexto["fotos"][0]
    assert nueva["estado_asociacion"] == "nota" and nueva["motivo"] == "cambio_identidad_fila"
    assert nueva["id"] == anterior["id"] and nueva["sha256"] == anterior["sha256"]
    correccion = respuesta(corregido, "Eso de 1640 era de la dos, no de la tres.", identificador="VOLVER")
    procesar(partes, worker, correccion)
    assert partes.obtener(nota.id).contexto["fotos"] == [nueva]
    captura = AlmacenMemoriaLocal(partes.ruta).siguiente().documento
    assert [b["contexto"]["fotos"][0] for b in captura["borradores"]] == [anterior, nueva, nueva]


def test_sin_pasos_para_foto_conserva_borrador_y_presupuesto_de_cierre(tmp_path):
    descargar = MagicMock()
    nota, _, _, partes, _ = iniciar(tmp_path, "La uno 112 kilos de descarga.", [FOTO], descargar,
                                    presupuesto=PresupuestoEjecucion(pasos=7))
    parte = partes.obtener(nota.id)
    assert parte.estado == "borrador_pendiente"
    assert parte.contexto["fotos"][0]["estado_descarga"] == "omitida_presupuesto"
    assert partes.estado_ejecucion(nota.id).consumo.pasos == 7
    descargar.assert_not_called()


@pytest.mark.parametrize("lugar", [
    "cerca de donde está la dos", "cerca de la dos", "al lado de la dos", "junto a la dos",
    "detrás de la dos", "delante de B-102", "frente a la dos", "debajo de la dos",
    "alrededor de la dos", "próximo a la dos", "en las inmediaciones de la dos",
    "cerca del motor de la dos", "cerca de la dos y de la tres",
])
def test_foto_cerca_de_equipo_no_demuestra_que_el_equipo_sea_el_objetivo(tmp_path, lugar):
    texto = f"La dos gotea por la empaquetadura. Saqué foto de un goteo {lugar}."
    descarga = MagicMock(return_value=("image/jpeg", JPEG))
    nota, _, _, partes, worker = iniciar(tmp_path, texto, [FOTO], descarga)
    parte = partes.obtener(nota.id)
    assert parte.estado == "borrador_pendiente"
    fila, = parte.filas
    assert (fila.tag, fila.variable) == ("B-102", "goteo_empaquetadura")
    evidencia, = parte.contexto["fotos"]
    assert evidencia["estado_asociacion"] == "nota" and evidencia["tag"] is None
    assert evidencia["objetivo_pendiente"] is None
    assert evidencia["estado_descarga"] == "descargada"
    procesar(partes, worker, respuesta(parte, "Confirmo"))
    _, _, _, reabierto, _ = preparar(tmp_path)
    final = reabierto.obtener(nota.id)
    assert final.estado == "confirmado" and final.filas == parte.filas
    assert final.contexto["fotos"] == [evidencia]
    assert reabierto.obtener_foto(evidencia["sha256"]) == ("image/jpeg", JPEG)
    descarga.assert_called_once()


@pytest.mark.parametrize(("referencia", "tag"), [
    ("del goteo de la dos", "B-102"),
    ("del goteo de B-102", "B-102"),
    ("del goteo de la dos al lado de la tres", "B-102"),
    ("del goteo de B-102, frente a B-103", "B-102"),
    ("del goteo de la dos cerca del motor de la tres", "B-102"),
    ("del goteo de la dos cerca de la tres y de la uno", "B-102"),
    ("del goteo de la dos cerca de la tres o de la uno", "B-102"),
    ("del goteo de la dos cerca de la tres y del goteo de la uno", None),
    ("del goteo de la dos cerca de la tres con otro goteo de la uno", None),
    ("del goteo de la dos y de la tres", None),
    ("de un goteo cerca de la tres, del motor de la dos", None),
])
def test_asociacion_distingue_objetivo_directo_de_referencias_espaciales(tmp_path, referencia, tag):
    texto = f"La dos gotea por la empaquetadura. La tres gotea por la empaquetadura. Saqué foto {referencia}."
    nota, _, _, partes, _ = iniciar(tmp_path, texto, [FOTO], None)
    parte = partes.obtener(nota.id)
    assert {f.tag for f in parte.filas} == {"B-102", "B-103"}
    foto, = parte.contexto["fotos"]
    assert foto["tag"] == tag
    assert foto["estado_asociacion"] == ("fila" if tag else "nota")


@pytest.mark.parametrize(("complemento", "variable"), [
    ("de la dos, el motor a 65 grados", None),
    ("de la dos y el motor a 65 grados", None),
    ("de la empaquetadura de la dos y el motor a 65 grados", "goteo_empaquetadura"),
    ("de la empaquetadura de la dos, la uno motor a 65 grados", "goteo_empaquetadura"),
])
def test_lectura_posterior_a_foto_no_identifica_objetivo_fotografico(tmp_path, complemento, variable):
    texto = f"La dos gotea por la empaquetadura y saqué foto {complemento}."
    nota, _, _, partes, _ = iniciar(tmp_path, texto, [FOTO], None)
    parte = partes.obtener(nota.id)
    tag_motor = "B-101" if "la uno" in complemento else "B-102"
    assert parte.estado == "borrador_pendiente"
    assert [(f.tag, f.variable, f.valor, f.unidad) for f in parte.filas] == [
        ("B-102", "goteo_empaquetadura", None, None),
        (tag_motor, "temperatura_motor_ir", 65, "degC"),
    ]
    foto, = parte.contexto["fotos"]
    assert foto["estado_asociacion"] == ("fila" if variable else "nota")
    assert (foto["tag"], foto["variable"]) == ("B-102" if variable else None, variable)
    assert foto["objetivo_pendiente"] is None


@pytest.mark.parametrize(("referencia", "asociable"), [
    ("del manómetro de descarga cerca de la dos", False),
    ("del manómetro de descarga de la dos junto a la tres", True),
])
def test_aclaracion_no_convierte_lugar_en_objetivo_fotografico(tmp_path, referencia, asociable):
    texto = f"La dos 1640 pc de descarga. Saqué foto {referencia}."
    descarga = MagicMock(return_value=("image/jpeg", JPEG))
    nota, _, _, partes, worker = iniciar(tmp_path, texto, [FOTO], descarga)
    inicial = partes.obtener(nota.id)
    assert inicial.estado == "aclaracion_pendiente"
    foto, = inicial.contexto["fotos"]
    assert foto["estado_asociacion"] == "nota"
    assert foto["objetivo_pendiente"] == ({"tag": "B-102", "variable": "presion_descarga_local"} if asociable else None)
    procesar(partes, worker, respuesta(inicial, "psi", identificador="ACLARA"))
    borrador = partes.obtener(nota.id)
    assert borrador.estado == "borrador_pendiente" and borrador.filas[0].unidad == "psi"
    resuelta, = borrador.contexto["fotos"]
    assert resuelta["tag"] == ("B-102" if asociable else None)
    assert resuelta["estado_asociacion"] == ("fila" if asociable else "nota")
    assert resuelta["objetivo_pendiente"] is None
    for campo in ("id", "sha256", "ruta", "ts", "mime", "bytes", "estado_descarga", "evidencia"):
        assert resuelta[campo] == foto[campo]
    descarga.assert_called_once()


def test_metadata_conserva_timestamp_original_aun_si_no_incluye_offset():
    nota = crear_nota()
    original = nota.ts.replace(tzinfo=None)
    nota = nota.model_copy(update={"fotos": [FOTO], "ts": original})
    fotos, archivos = capturar_fotos(nota, "", {}, [], None, None, None)
    assert fotos[0]["ts"] == original.isoformat() and not archivos


def test_foto_ya_persistida_sin_objetivo_conserva_sus_metadatos():
    nota = crear_nota().model_copy(update={"fotos": [FOTO]})
    fotos, _ = capturar_fotos(nota, "", {}, [], None, None, None)
    fotos[0].pop("objetivo_pendiente")
    assert reconsiderar_fotos(fotos, []) == fotos


@pytest.mark.parametrize(("texto", "tipo", "texto_respuesta", "tag", "variable"), [
    (NOTA_UNIDAD, "unidad", "psi", "B-102", "presion_descarga_local"),
    ("El filtro A me dio dieciocho de diferencial. Saqué foto del manómetro diferencial del F-201A.",
     "valor", "uno coma ocho", "F-201A", "presion_diferencial"),
])
def test_aclaracion_asocia_foto_tras_reapertura_sin_descargar_de_nuevo(
    tmp_path, monkeypatch, texto, tipo, texto_respuesta, tag, variable,
):
    @contextmanager
    def http_foto(metodo, url, **opciones):
        yield httpx.Response(200, headers={"content-type": "image/jpeg",
                                          "content-length": str(len(JPEG))},
                             content=JPEG, request=httpx.Request(metodo, url))

    stream = MagicMock(side_effect=http_foto)
    monkeypatch.setattr(httpx, "stream", stream)
    descargar = ClientePlanta("http://sim:8100").foto_parte
    nota, _, _, partes, _ = iniciar(tmp_path, texto, [FOTO], descargar)
    inicial = partes.obtener(nota.id)
    assert inicial.estado == "aclaracion_pendiente" and inicial.contexto["pendientes"][0]["tipo"] == tipo
    anterior, = inicial.contexto["fotos"]
    assert anterior["estado_asociacion"] == "nota" and anterior["estado_descarga"] == "descargada"
    assert anterior["motivo"] == "fila_pendiente"
    assert anterior["evidencia"]
    assert partes.obtener_foto(anterior["sha256"]) == ("image/jpeg", JPEG)

    _, _, _, partes, _ = preparar(tmp_path)
    worker = WorkerPartes(partes, definicion_planta, foto=descargar)
    procesar(partes, worker, respuesta(inicial, texto_respuesta, identificador="ACLARACION"))
    borrador = partes.obtener(nota.id)
    asociada, = borrador.contexto["fotos"]
    assert borrador.estado == "borrador_pendiente" and borrador.version == 2
    assert asociada["estado_asociacion"] == "fila"
    assert (asociada["tag"], asociada["variable"]) == (tag, variable)
    for clave in ("id", "sha256", "ruta", "ts", "mime", "bytes", "estado_descarga", "evidencia"):
        assert asociada[clave] == anterior[clave]
    procesar(partes, worker, respuesta(inicial, texto_respuesta, identificador="TARDIA"))
    assert resultado(partes, "TARDIA") == "desfasada" and partes.obtener(nota.id) == borrador
    confirmacion = respuesta(borrador, "Confirmo", identificador="FINAL")
    procesar(partes, worker, confirmacion)
    assert not partes.guardar_respuesta(confirmacion)
    assert partes.obtener(nota.id).estado == "confirmado"
    captura = AlmacenMemoriaLocal(partes.ruta).siguiente().documento
    assert [b["contexto"]["fotos"][0] for b in captura["borradores"]] == [anterior, asociada]
    stream.assert_called_once()
    assert stream.call_args.args == ("GET", f"http://sim:8100{FOTO}")


@pytest.mark.parametrize(("objetivo", "equipo", "rutas", "estado", "motivo"), [
    ("la dos", "la dos", [FOTO], "fila", None),
    ("la dos", "la uno", [FOTO], "nota", "fila_pendiente"),
    ("la bomba", "la dos", [FOTO], "nota", "sin_mencion_inequivoca"),
    ("la dos o de la tres", "la dos", [FOTO], "nota", "mencion_ambigua"),
    ("la dos", "la dos", [FOTO, "/media/fotos/otra.jpg"], "nota", "varias_referencias_sin_correspondencia"),
])
def test_aclaracion_de_equipo_respeta_el_objetivo_original(tmp_path, objetivo, equipo, rutas, estado, motivo):
    texto = f"La bomba está en 112 de descarga. Saqué foto del manómetro de descarga de {objetivo}."
    nota, _, _, partes, worker = iniciar(tmp_path, texto, rutas, None)
    procesar(partes, worker, respuesta(partes.obtener(nota.id), f"Era {equipo}."))
    parte = partes.obtener(nota.id)
    assert parte.estado == "borrador_pendiente"
    assert all((f["estado_asociacion"], f["motivo"]) == (estado, motivo) for f in parte.contexto["fotos"])


def test_presupuesto_sin_paso_opcional_publica_borrador_y_conserva_objetivo(tmp_path, caplog):
    descargar = MagicMock(return_value=("image/jpeg", JPEG))
    nota, _, _, partes, worker = iniciar(tmp_path, NOTA_UNIDAD, [FOTO], descargar,
                                        presupuesto=PresupuestoEjecucion(pasos=17))
    inicial = partes.obtener(nota.id)
    with caplog.at_level("INFO", logger="sistema.execution_budget"):
        procesar(partes, worker, respuesta(inicial, "psi"))
    borrador = partes.obtener(nota.id)
    assert borrador.estado == "borrador_pendiente"
    assert borrador.filas[0].unidad == "psi"
    assert borrador.contexto["fotos"] == inicial.contexto["fotos"]
    assert partes.estado_ejecucion(nota.id).consumo.pasos == 17
    assert "reconsiderar_asociacion_foto" not in caplog.text
    descargar.assert_called_once()


def test_limite_de_interacciones_conserva_foto_pendiente_si_descarta_filas_nuevas(tmp_path):
    nota, _, _, partes, worker = iniciar(tmp_path, NOTA_UNIDAD, [FOTO], None)
    inicial = partes.obtener(nota.id)
    procesar(partes, worker, respuesta(inicial, "No sé", identificador="R1"))
    procesar(partes, worker, respuesta(inicial, "Repetime", identificador="R2"))
    procesar(partes, worker, respuesta(inicial, "psi", identificador="R3"))
    final = partes.obtener(nota.id)
    assert final.estado == "revision_manual" and final.filas == inicial.filas
    assert final.contexto["fotos"] == inicial.contexto["fotos"]
    assert resultado(partes, "R3") == "limite_interacciones"
