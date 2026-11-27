import os
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from scripts.evaluar_rag_retrieval import _adaptar, evaluar_variante
from sistema.plant_tool_models import ErrorTool
from sistema.plant_tools import CatalogoToolsPlanta
from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres
from sistema.rag_embeddings import ModeloEmbedding
from sistema.rag_retrieval import ConsultaManuales, RecuperadorPostgres, ResultadoManuales, _parametros


CORPUS = "1" * 64
CONFIG = ConfiguracionPostgres("localhost", 5432, "pia3", "pia3_app", "clave-de-prueba")


@pytest.mark.parametrize("datos", [
    {}, {"consulta": ""}, {"consulta": " \n\t "}, {"consulta": "?"}, {"consulta": "m" * 401},
    {"consulta": "aceite\0valido"}, {"consulta": "aceite", "otro": 1},
    {"consulta": "aceite", "top_k": "2"}, {"consulta": "aceite", "top_k": 21},
    {"consulta": "aceite", "filtros": {"equipo": "B-101 OR true"}},
    {"consulta": "aceite", "filtros": {"documento": "../manual"}},
    {"consulta": "aceite", "filtros": {"modelo": "TX-350", "sql": "SELECT 1"}},
    {"consulta": "aceite", "filtros": {"vigencia": False}},
    {"consulta": "aceite", "filtros": {"aplicabilidad": "no_aplicable"}},
])
def test_argumentos_estrictos(datos):
    with pytest.raises(ValidationError):
        ConsultaManuales.model_validate(datos)


def test_catalogo_expone_tool_sin_abrir_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("PIA3_RAG_CORPUS_ID", raising=False)
    catalogo = CatalogoToolsPlanta()
    assert len(catalogo.catalogo()) == 8
    assert catalogo.ejecutar("consultar_stock", {"codigo": "TX-VAL-KIT"})
    with pytest.raises(ErrorTool) as error:
        catalogo.ejecutar("buscar_manuales", {"consulta": "aceite"})
    assert error.value.codigo == "configuracion_invalida"


def test_tool_delega_y_distingue_resultado_vacio():
    class Vacio:
        def buscar(self, consulta):
            assert consulta.consulta == "aceite del cárter"
            assert consulta.filtros.equipo == "B-103"
            return ResultadoManuales(modo="textual_postgresql", corpus=CORPUS, resultados=[])

    tool = CatalogoToolsPlanta(retriever=Vacio())
    assert tool.ejecutar("buscar_manuales", {
        "consulta": "  aceite del cárter  ", "filtros": {"equipo": "B-103"},
    }) == {"modo": "textual_postgresql", "corpus": CORPUS, "resultados": []}


@pytest.mark.parametrize(("excepcion", "codigo", "intentos"), [
    (ErrorConexionPostgres, "fuente_no_disponible", 1),
    (psycopg.errors.QueryCanceled, "tiempo_agotado", 1),
    (psycopg.errors.UndefinedTable, "error_retrieval", 1),
    (psycopg.errors.SerializationFailure, "fuente_no_disponible", 2),
])
def test_fallos_controlados_y_reintento_acotado(monkeypatch, excepcion, codigo, intentos):
    llamadas = []

    @contextmanager
    def fallar(config, *, timeout_sentencia_ms):
        llamadas.append(timeout_sentencia_ms)
        raise excepcion("mensaje privado del servidor")
        yield

    monkeypatch.setattr("sistema.rag_retrieval.conectar_postgres", fallar)
    with pytest.raises(ErrorTool) as error:
        RecuperadorPostgres(CONFIG, CORPUS).buscar(ConsultaManuales(consulta="cárter"))
    assert error.value.codigo == codigo
    assert "privado" not in str(error.value)
    assert len(llamadas) == intentos and all(0 < t <= 3000 for t in llamadas)


def test_espacio_sintetico_se_rechaza_antes_de_conectar(monkeypatch):
    def conectar(*args, **kwargs):
        pytest.fail("La búsqueda semántica abrió conexión para un modelo de prueba")

    monkeypatch.setattr("sistema.rag_retrieval.conectar_postgres", conectar)
    modelo = ModeloEmbedding(nombre="prueba", revision="1", dimensiones=3, ambito="prueba", configuracion={})
    with pytest.raises(ErrorTool) as error:
        RecuperadorPostgres(CONFIG, CORPUS).verificar_espacio_semantico(modelo)
    assert error.value.codigo == "espacio_no_disponible"


def test_adaptador_del_evaluador_deduplica_y_separa_orden_de_distractores():
    llamadas = []
    suite = [{"pregunta": "prueba", "distractores": ["TX350-C §A"], "relevantes": ["TX350-D §A"]}]

    def oficial(buscar, preguntas, ks):
        assert ks == (1, 3, 5, 10) and preguntas == suite
        assert buscar("prueba", 20) == ["TX350-C §A", "TX350-D §A"]
        return {"preguntas": [{
            "id": "prueba", "tipo": "prueba", "mrr": 0.0, "hit@10": 0,
            **{f"distractor@{k}": 0 for k in ks},
        }], "por_tipo": {"prueba": {}}}

    def buscar(pregunta, k):
        llamadas.append((pregunta, k))
        return ["TX350-C §A", "TX350-C §A", "TX350-D §A"]

    informe = evaluar_variante(oficial, buscar, suite)
    assert llamadas == [("prueba", 20)]
    assert informe["rev_C_antes_D"] == 1 and informe["rev_C_sin_D"] == 0
    assert informe["rev_C_distractora_antes_D_relevante"] == 1
    assert informe["por_tipo"]["prueba"]["distractor@5"] == 0
    assert informe["sin_acierto_20"] == ["prueba"]


def test_adaptador_transmite_consulta_y_k():
    class Resultado:
        resultados = []

    def buscar(consulta):
        assert consulta.consulta == "consulta de prueba" and consulta.top_k == 20
        return Resultado()

    assert _adaptar(buscar)("consulta de prueba", 20) == []


def test_parametros_conservan_cita_al_final_de_oracion_y_orden():
    consulta = ConsultaManuales(consulta="Ver TX350-D §A.1. Comparar historial OT-24-0001.")
    parametros = _parametros(consulta, CORPUS, 701)
    assert parametros["citas"] == ["historial ot-24-0001", "tx350-d §a.1"]


@pytest.fixture
def recuperador():
    if os.getenv("PIA3_RETRIEVAL_INTEGRATION") != "1":
        pytest.skip("Requiere PostgreSQL aislado con el corpus oficial ingresado")
    return RecuperadorPostgres.desde_entorno()


def _buscar(recuperador, consulta, *, top=5, **filtros):
    return recuperador.buscar(ConsultaManuales.model_validate({
        "consulta": consulta, "top_k": top, "filtros": filtros,
    })).resultados


def test_consulta_real_con_filtros_combinados_y_orden_estable(recuperador):
    filtros = dict(equipo="B-101", fabricante="trimax industrial", modelo="TX-350",
                   documento="TX350-D", tipo_documento="manual_fabricante", vigencia="vigente")
    resultados = _buscar(recuperador, "TX350-D §A.1 aceite del cárter", top=3, **filtros)
    assert len(resultados) == 3 and len({r.cita for r in resultados}) == 3
    assert resultados[0].cita == "TX350-D §A.1"
    assert resultados[0].seccion == "A.1" and resultados[0].pagina is not None
    assert all(r.vigente and r.revision == "D" and r.documento == "TX350-D" for r in resultados)
    assert [r.posicion for r in resultados] == [1, 2, 3]
    assert resultados == _buscar(recuperador, "TX350-D §A.1 aceite del cárter", top=3, **filtros)


def test_vigencia_aplicabilidad_alcance_general_e_historial(recuperador):
    assert all(r.vigente is not False for r in _buscar(recuperador, "TX350-C §3.2"))
    obsoletos = _buscar(recuperador, "TX350-C §3.2", vigencia="obsoleta", documento="TX350-C")
    assert obsoletos[0].cita == "TX350-C §3.2" and obsoletos[0].vigente is False
    comparacion = _buscar(recuperador, "FC48-3 §2.1 FC-24 FC-96", equipo="F-201A", aplicabilidad="confirmada")
    assert comparacion[0].cita == "FC48-3 §2.1"
    assert comparacion[0].aplicabilidad.referencias == ("FC-24", "FC-96")
    aplicables = _buscar(recuperador, "MBP-4 §17.1", equipo="B-102", documento="MBP-4", top=20)
    assert aplicables and all(not r.cita.startswith("MBP-4 §17") for r in aplicables)
    generales = _buscar(recuperador, "MBP-4 §6.1", equipo="F-201B")
    assert generales[0].cita == "MBP-4 §6.1" and generales[0].aplicabilidad.estado == "indeterminada"
    ot = _buscar(recuperador, "OT-24-0001", equipo="B-101", tipo_documento="historial")[0]
    assert ot.cita == "historial OT-24-0001" and ot.pagina is None and ot.vigente is None
    assert not _buscar(recuperador, "OT-24-0001", tipo_documento="historial", vigencia="vigente")


@pytest.mark.parametrize("codigo", ["FC-T02", "TX-T07", "F10"])
def test_mapa_oficial_de_codigos_y_texto_de_repuestos(recuperador, codigo):
    mapa = _buscar(recuperador, codigo, top=1, documento="MBP-4")
    assert mapa[0].cita == "MBP-4 §6.1" and codigo in mapa[0].texto
    repuesto = _buscar(recuperador, "TX-VAL-KIT", tipo_documento="manual_fabricante")
    assert any("TX-VAL-KIT" in fila.texto for fila in repuesto)


def test_corpus_inexistente_y_conexion_de_solo_lectura(recuperador):
    config = ConfiguracionPostgres.desde_entorno()
    with pytest.raises(ErrorTool) as error:
        _buscar(RecuperadorPostgres(config, "f" * 64), "aceite")
    assert error.value.codigo == "corpus_no_disponible"
    with recuperador._lectura() as db:
        assert db.execute("SHOW transaction_read_only").fetchone() == ("on",)
        assert db.execute("SHOW statement_timeout").fetchone() == ("2500ms",)
    assert not _buscar(recuperador, "zzinexistenteqx")


def test_espacio_operativo_incompleto_e_identidad_incompatible(recuperador):
    from sistema.postgres import conectar_postgres

    modelo = ModeloEmbedding(nombre="solo-metadatos-prueba", revision="1", dimensiones=3,
                             ambito="operativo", configuracion={})
    config = ConfiguracionPostgres.desde_entorno()
    with conectar_postgres(config) as db:
        class LectorEnTransaccion(RecuperadorPostgres):
            @contextmanager
            def _lectura(self):
                yield db

        db.execute(
            """
            INSERT INTO rag.indice (corpus_id, espacio, ambito, dimensiones, modelo)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT (corpus_id, espacio) DO NOTHING
            """, (recuperador.corpus, modelo.identidad, modelo.ambito, modelo.dimensiones,
                  Jsonb(modelo.model_dump(mode="json"))),
        )
        lector = LectorEnTransaccion(config, recuperador.corpus)
        for esperado in (modelo, modelo.model_copy(update={"revision": "2"})):
            with pytest.raises(ErrorTool) as error:
                lector.verificar_espacio_semantico(esperado)
            assert error.value.codigo == "espacio_no_disponible"
        db.rollback()
