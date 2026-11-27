import os
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import psycopg
import pytest
from pydantic import ValidationError

from sistema.postgres import ConfiguracionPostgres, conectar_postgres
from sistema.rag_corpus import (
    ErrorCorpus,
    FragmentoFuente,
    OrdenFuente,
    correspondencias_fallas,
    leer_corpus,
    preparar_corpus,
)
from sistema.rag_embeddings import ErrorEmbedding, ModeloEmbedding, generar_vectores
from sistema.rag_store import consultar_estado, ingestar


@pytest.fixture
def fragmento():
    return FragmentoFuente(
        id="TX350-D §3.3", doc="TX350-D", numero="3.3", titulo="Otros modelos",
        clave="limites-otros", pagina=8, vigente=True,
        texto="Estos valores no aplican a la TX-350.\nVariable | TX-250 | TX-500",
    )


def test_campos_fuente_estrictos(fragmento):
    dato = fragmento.model_dump()
    for cambio in ({"pagina": "8"}, {"vigente": 1}, {"texto": " "}, {"respuesta": "x"}):
        with pytest.raises(ValidationError):
            FragmentoFuente.model_validate(dato | cambio)


def test_orden_no_inventa_datos_ausentes():
    orden = OrdenFuente(id="OT-24-0001", equipo="PLANTA")
    assert orden.codigo_falla is None
    assert orden.fecha_cierre is None
    assert orden.tareas is None


def test_excluye_archivos_de_evaluacion():
    archivo = BytesIO()
    with ZipFile(archivo, "w") as zip:
        zip.writestr("evaluacion/retrieval.json", "{}")
    with pytest.raises(ErrorCorpus, match="archivos"):
        leer_corpus(archivo.getvalue())


def test_codigos_solo_desde_la_columna_fabricante(fragmento):
    tabla = fragmento.model_copy(update={
        "id": "MBP-4 §6.1",
        "texto": "Código | Falla | Fabricante | Tarea | Ejecutor\n"
                 "F10 | Sensor TX-T99 | TX-T08 / FC-T04 | T-I-CAL | propio",
    })
    assert [(c.codigo_fabricante, c.falla, c.cita) for c in correspondencias_fallas(tabla)] == [
        ("TX-T08", "F10", "MBP-4 §6.1"), ("FC-T04", "F10", "MBP-4 §6.1"),
    ]
    with pytest.raises(ErrorCorpus, match="fabricante"):
        correspondencias_fallas(tabla.model_copy(update={"texto": tabla.texto.replace("FC-T04", "?")}))


class ProveedorPrueba:
    modelo = ModeloEmbedding(
        nombre="sintetico-prueba", revision="1", dimensiones=3, ambito="prueba",
        configuracion={"normalizacion": "ninguna"},
    )
    max_tokens = 512

    def contar_tokens(self, texto):
        return len(texto.split())

    def documentos(self, textos, *, timeout_s):
        return [[1.0, 2.0, 3.0] for _ in textos]

    def consultas(self, textos, *, timeout_s):
        return self.documentos(textos, timeout_s=timeout_s)


@pytest.mark.parametrize("vectores", [
    [], [[1.0]], [[float("nan"), 1, 2]], [[float("inf"), 1, 2]], [[True, 1, 2]],
    [[0, 0, 0]], [[1e60, 1, 2]], [[1e-80, 0, 0]],
])
def test_rechaza_embeddings_invalidos(monkeypatch, vectores):
    proveedor = ProveedorPrueba()
    monkeypatch.setattr(proveedor, "documentos", lambda *args, **kwargs: vectores)
    with pytest.raises(ErrorEmbedding):
        generar_vectores(proveedor, proveedor.modelo, ["documento"])


def test_compatibilidad_de_espacio_y_limite_tokens():
    proveedor = ProveedorPrueba()
    esperado = proveedor.modelo.model_copy(update={"revision": "2"})
    assert esperado.identidad != proveedor.modelo.identidad
    with pytest.raises(ErrorEmbedding, match="incompatible"):
        generar_vectores(proveedor, esperado, ["documento"])
    with pytest.raises(ErrorEmbedding, match="tokens"):
        generar_vectores(proveedor, proveedor.modelo, ["x " * 513])
    assert generar_vectores(proveedor, proveedor.modelo, ["documento"]) == [[1, 2, 3]]


def test_proveedor_fallido_no_expone_detalles(monkeypatch):
    proveedor = ProveedorPrueba()

    def fallar(*args, **kwargs):
        raise TimeoutError("detalle sensible")

    monkeypatch.setattr(proveedor, "documentos", fallar)
    with pytest.raises(ErrorEmbedding, match="proveedor") as error:
        generar_vectores(proveedor, proveedor.modelo, ["documento"])
    assert "sensible" not in str(error.value)


def test_proveedor_no_puede_cambiar_configuracion_durante_un_lote(monkeypatch):
    proveedor = ProveedorPrueba()
    modelo = proveedor.modelo.model_copy(deep=True)
    monkeypatch.setattr(proveedor, "modelo", modelo)

    def cambiar_configuracion(*args, **kwargs):
        modelo.configuracion["normalizacion"] = "nueva"
        return [[1.0, 2.0, 3.0]]

    monkeypatch.setattr(proveedor, "documentos", cambiar_configuracion)
    with pytest.raises(ErrorEmbedding, match="cambió de espacio"):
        generar_vectores(proveedor, modelo, ["documento"])


@pytest.fixture
def corpus_oficial():
    ruta = os.getenv("PIA3_RAG_CORPUS")
    if not ruta:
        pytest.skip("Requiere PIA3_RAG_CORPUS con un paquete oficial exportado")
    return leer_corpus(Path(ruta).read_bytes())


def test_corpus_oficial_preserva_identidad_y_metadatos(corpus_oficial):
    corpus = preparar_corpus(corpus_oficial)
    assert len(corpus.documentos) == 7
    assert len(corpus.chunks) == 701
    docs = {d.id: d for d in corpus.documentos}
    chunks = {c.id: c for c in corpus.chunks}
    assert docs["TX350-D"].vigente and not docs["TX350-C"].vigente
    assert docs["TX350-C"].reemplazado_por == "MT-TX350-ES Rev. D (2023-03-15)"
    assert docs["TX350-D"].fabricante == "Trimax Industrial"
    assert docs["TX350-D"].modelos == ("TX-350",)
    assert chunks["TX350-D §3.3"].aplicabilidad.estado == "no_aplicable"
    assert chunks["TX350-D §3.3"].equipos == ()
    assert chunks["TX350-D §2.1"].aplicabilidad.estado == "confirmada"
    assert chunks["TX350-D §2.1"].alcance_equipos == "documento"
    assert chunks["MBP-4 §6.1"].aplicabilidad.estado == "indeterminada"
    comparativo = chunks["FC48-3 §2.1"]
    assert comparativo.aplicabilidad.referencias == ("FC-24", "FC-96")
    assert comparativo.id == comparativo.cita and comparativo.texto in comparativo.texto_embedding
    assert chunks["MBP-4 §17.2"].aplicabilidad.cita == "MBP-4 §17"
    assert chunks["MBP-4 §5.3"].aplicabilidad.estado == "no_aplicable"
    assert chunks["MBP-4 §1.1"].aplicabilidad.estado == "confirmada"
    assert chunks["CT-SVP-2025 §1.1"].aplicabilidad.estado == "confirmada"
    assert len(chunks["MBP-4 §6.1"].codigos_falla) == 11
    ot = chunks["historial OT-24-0001"]
    assert ot.pagina is None and ot.seccion is None and ot.vigente is None
    assert ot.orden is not None and ot.orden.codigo_falla is None
    assert ot.alcance_equipos == "orden"
    assert "tecnico" not in ot.orden.model_dump()


def test_rechaza_id_duplicado_o_referencia_inconsistente(corpus_oficial):
    fragmento, *resto = corpus_oficial.fragmentos
    with pytest.raises(ErrorCorpus, match="duplicado"):
        preparar_corpus(replace(corpus_oficial, fragmentos=(fragmento, fragmento, *resto)))
    invalido = fragmento.model_copy(update={"pagina": 999})
    with pytest.raises(ErrorCorpus, match="sección"):
        preparar_corpus(replace(corpus_oficial, fragmentos=(invalido, *resto)))


@pytest.fixture
def postgres_rag():
    if os.getenv("PIA3_RAG_INTEGRATION") != "1":
        pytest.skip("Requiere una instancia aislada con 003 y PIA3_RAG_INTEGRATION=1")
    return ConfiguracionPostgres.desde_entorno()


@pytest.fixture
def indice_prueba(postgres_rag, corpus_oficial):
    corpus = preparar_corpus(corpus_oficial)
    proveedor = ProveedorPrueba()
    ingestar(postgres_rag, corpus, proveedor=proveedor, modelo=proveedor.modelo)
    return corpus, proveedor


def test_ingesta_real_repetible_y_consulta_vectorial(postgres_rag, indice_prueba, monkeypatch):
    corpus, proveedor = indice_prueba
    monkeypatch.setattr(proveedor, "documentos", lambda *a, **k: pytest.fail("Repite inferencia"))
    assert not ingestar(postgres_rag, corpus, proveedor=proveedor, modelo=proveedor.modelo)
    assert not ingestar(postgres_rag, corpus)
    (estado,) = consultar_estado(postgres_rag, corpus.identidad, verificar=True)
    assert estado["documentos_tecnicos"] == 6
    assert estado["documentos_historial"] == 1
    assert estado["secciones"] == 458 and estado["ordenes"] == 243 and estado["chunks"] == 701
    assert estado["documentos_por_vigencia"] == {"vigente": 5, "no_vigente": 1, "sin_vigencia": 1}
    assert estado["embeddings_prueba"] == 701 and estado["embeddings_operativos"] == 0
    assert estado["espacios"][0]["completo"]
    with conectar_postgres(postgres_rag) as db:
        fila = db.execute(
            """
            SELECT chunk_id, vector_dims(vector), vector <-> %s::vector
            FROM rag.embedding WHERE corpus_id = %s AND espacio = %s AND chunk_id = %s
            """,
            ("[1,2,5]", corpus.identidad, proveedor.modelo.identidad, "TX350-D §3.3"),
        ).fetchone()
        assert fila == ("TX350-D §3.3", 3, 2.0)
        assert db.execute(
            """
            SELECT texto_busqueda @@ plainto_tsquery('spanish', 'cavitación')
            FROM rag.chunk WHERE corpus_id = %s AND id = 'MBP-4 §6.11'
            """, (corpus.identidad,),
        ).fetchone() == (True,)
        assert db.execute("SELECT to_regclass('rag.rag_chunk_texto_gin') IS NOT NULL").fetchone() == (True,)


def test_fallo_en_actualizacion_conserva_indice_previo(postgres_rag, indice_prueba, monkeypatch):
    anterior, proveedor = indice_prueba
    antes = consultar_estado(postgres_rag, anterior.identidad, verificar=True)
    nuevo = replace(anterior, manifiesto=anterior.manifiesto.model_copy(update={"sha_simulador": "e" * 40}))
    monkeypatch.setattr(proveedor, "documentos", lambda *a, **k: [[1.0]])
    with pytest.raises(ErrorEmbedding):
        ingestar(postgres_rag, nuevo, proveedor=proveedor, modelo=proveedor.modelo)
    with pytest.raises(ErrorCorpus, match="no está ingresado"):
        consultar_estado(postgres_rag, nuevo.identidad)

    incorrecto = replace(nuevo, chunks=(
        nuevo.chunks[0].model_copy(update={"texto": ""}), *nuevo.chunks[1:],
    ))
    with pytest.raises(psycopg.errors.CheckViolation):
        ingestar(postgres_rag, incorrecto)
    with pytest.raises(ErrorCorpus, match="no está ingresado"):
        consultar_estado(postgres_rag, nuevo.identidad)
    assert consultar_estado(postgres_rag, anterior.identidad, verificar=True) == antes


def test_permisos_y_dimension_en_postgres(postgres_rag, indice_prueba):
    corpus, proveedor = indice_prueba
    with conectar_postgres(postgres_rag) as db:
        assert db.execute(
            """
            SELECT has_schema_privilege('rag', 'CREATE'),
                   has_table_privilege('rag.chunk', 'UPDATE, DELETE, TRUNCATE'),
                   has_table_privilege('memoria.partes', 'SELECT'),
                   to_regclass('memoria.partes') IS NOT NULL
            """,
        ).fetchone() == (False, False, True, True)
        with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
            db.execute(
                """
                INSERT INTO rag.embedding (corpus_id, espacio, chunk_id, dimensiones, vector)
                VALUES (%s, %s, %s, 3, '[1,2]'::vector)
                """, (corpus.identidad, proveedor.modelo.identidad, corpus.chunks[0].id),
            )
