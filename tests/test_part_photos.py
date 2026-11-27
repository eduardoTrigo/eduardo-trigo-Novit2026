import sqlite3
import time
from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest

from sistema.part_context import ClientePlanta, MAX_BYTES_FOTO_B1
from sistema.execution_budget import PresupuestoEjecucion
from sistema.part_memory_store import AlmacenMemoriaLocal
from sistema.part_photos import capturar_fotos
from sistema.part_worker import WorkerPartes
from test_part_extraction import definicion_planta
from test_part_messages import preparar, transcribir_nota
from test_part_workflow import procesar, respuesta
from test_security_messages import crear_nota


FOTO = "/media/fotos/prueba.jpg"
JPEG = b"\xff\xd8\xffprueba\xff\xd9"
NOTA_CON_FOTO = ("La uno 112 kilos de descarga. La dos gotea por la empaquetadura. "
                 "Saqué foto de la empaquetadura de la dos.")


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
    captura = AlmacenMemoriaLocal(partes.ruta).siguiente().documento
    assert [b["contexto"]["fotos"][0] for b in captura["borradores"]] == [anterior, nueva]


def test_sin_pasos_para_foto_conserva_borrador_y_presupuesto_de_cierre(tmp_path):
    descargar = MagicMock()
    nota, _, _, partes, _ = iniciar(tmp_path, "La uno 112 kilos de descarga.", [FOTO], descargar,
                                    presupuesto=PresupuestoEjecucion(pasos=7))
    parte = partes.obtener(nota.id)
    assert parte.estado == "borrador_pendiente"
    assert parte.contexto["fotos"][0]["estado_descarga"] == "omitida_presupuesto"
    assert partes.estado_ejecucion(nota.id).consumo.pasos == 7
    descargar.assert_not_called()


def test_foto_cerca_de_equipo_no_demuestra_que_el_equipo_sea_el_objetivo(tmp_path):
    texto = "La dos gotea por la empaquetadura. Saqué foto de un goteo cerca de donde está la dos."
    nota, _, _, partes, _ = iniciar(tmp_path, texto, [FOTO], None)
    parte = partes.obtener(nota.id)
    assert parte.estado == "borrador_pendiente"
    evidencia, = parte.contexto["fotos"]
    assert evidencia["estado_asociacion"] == "nota" and evidencia["tag"] is None


def test_metadata_conserva_timestamp_original_aun_si_no_incluye_offset():
    nota = crear_nota()
    original = nota.ts.replace(tzinfo=None)
    nota = nota.model_copy(update={"fotos": [FOTO], "ts": original})
    fotos, archivos = capturar_fotos(nota, "", {}, [], None, None, None)
    assert fotos[0]["ts"] == original.isoformat() and not archivos
