import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from sistema.plant_tool_models import Equipo, ErrorTool
from sistema.plant_tools import CatalogoToolsPlanta, cargar_referencia


DESDE = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
HASTA = DESDE + timedelta(hours=1)
SENSOR = {"tag": "B-102", "variable": "presion_descarga", "unidad": "kg/cm2", "alarma_alta": 125}
EQUIPO = {"tag": "B-102", "tipo": "bomba", "descripcion": "Bomba 2", "nombres": ["la dos"],
          "manometro_local": {"unidad": "psi"}}
HISTORIAN = {"tag": "B-102", "variable": "presion_descarga", "desde": DESDE, "hasta": HASTA}


@pytest.fixture
def tool_http():
    requests = []
    resultados = []

    def responder(request):
        requests.append(request)
        respuesta = resultados.pop(0)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    return CatalogoToolsPlanta(transport=httpx.MockTransport(responder)), requests, resultados


def test_equipos_valida_publicacion_y_reune_sensores(tool_http):
    tools, requests, resultados = tool_http
    resultados.append(httpx.Response(200, json={"equipos": [EQUIPO], "sensores": [SENSOR]}))
    datos = tools.ejecutar("consultar_equipos", {"tag": "B-102", "tipo": "bomba"})
    equipo = datos["items"][0]
    assert equipo["nombres"] == ["la dos"] and equipo["manometro_local"]["unidad"] == "psi"
    assert equipo["sensores"] == [SENSOR] and datos["truncado"] is False
    assert [(r.method, r.url.host, r.url.path) for r in requests] == [("GET", "sim", "/api/planta")]


def test_historian_usa_iso_de_planta_y_trunca(tool_http):
    tools, requests, resultados = tool_http
    resultados.append(httpx.Response(200, json=[{"ts": "2026-11-20T09:01:00-03:00", "valor": 113.5},
                                               {"ts": "2026-11-20T09:00:00-03:00", "valor": None}]))
    datos = tools.ejecutar("consultar_historial_sensores", '''{
        "tag":"B-102","variable":"presion_descarga","desde":"2026-11-20T12:00:00Z",
        "hasta":"2026-11-20T10:00-03:00","limite":1}''')
    assert datos["items"] == [{"ts": "2026-11-20T09:00:00-03:00", "valor": None}]
    assert datos["unidad"] == "kg/cm2" and datos["total"] == 2 and datos["truncado"] is True
    assert requests[0].url.path == "/api/historian/B-102/presion_descarga"
    assert requests[0].url.params["desde"] == "2026-11-20T09:00:00-03:00"


def test_ordenes_conserva_id_origen_y_filtros(tool_http):
    tools, requests, resultados = tool_http
    indice = {"equipo": "B-102", "tareas": ["T-B-EMP"], "estado": "Cerrada", "ejecutor": "propio"}
    resultados.append(httpx.Response(200, json=[
        dict(indice, id="OT-A", origen="archivo", fecha="2026-11-20T09:05-03:00", solicitante="dato omitido"),
        dict(indice, id="OT-B", origen="simulador", fecha="2026-11-20T09:10:00-03:00"),
    ]))
    consulta = {"equipo": "B-102", "desde": DESDE, "hasta": HASTA, "limite": 1}
    datos = tools.ejecutar("consultar_historial_ordenes", consulta)
    assert [(o["id"], o["origen"]) for o in datos["items"]] == [("OT-B", "simulador")]
    assert datos["total"] == 2 and datos["truncado"]
    assert requests[0].url.path == "/api/historial-ordenes" and requests[0].url.params["equipo"] == "B-102"


def test_inventario_stock_materiales_y_tareas_sin_http(tool_http):
    tools, requests, _ = tool_http
    inventario = tools.ejecutar("consultar_inventario", {"codigo": "TX-ROD-CIG"})
    assert inventario["origen"] == "referencia" and inventario["items"][0]["costo_usd"] == 2650
    stock = tools.ejecutar("consultar_stock", {"codigo": "TX-ROD-CIG", "almacen": "yacimiento"})
    assert stock["stock"] == {"yacimiento": 0}
    lista = tools.ejecutar("consultar_lista_materiales", {"equipo": "F-201A", "limite": 1})
    assert lista["items"][0]["codigo"] == "FC-CART-25" and lista["truncado"]
    assert tools.ejecutar("consultar_lista_materiales", {"equipo": "TK-301"})["estado"] == "sin_lista"
    tareas = tools.ejecutar("consultar_catalogo_tareas", {"codigo": "T-F-CAR"})["items"]
    assert [(t["equipos"], t["repuestos"][0]["codigo"]) for t in tareas] == [
        (["F-201A"], "FC-CART-25"), (["F-201B"], "FC-CART-10"),
    ]
    no_estandar = tools.ejecutar("consultar_catalogo_tareas", {"codigo": "I-VAL"})["items"][0]
    assert no_estandar["clase"] == "no_estandar" and no_estandar["ejecutor"] == "propio"
    assert "repuestos" not in no_estandar and "equipos" not in no_estandar
    assert not requests


@pytest.mark.parametrize("nombre,args", [
    ("consultar_equipos", {"tag": 102}), ("consultar_equipos", {"tag": "../api/estado"}),
    ("consultar_historial_sensores", HISTORIAN | {"desde": HASTA, "hasta": DESDE}),
    ("consultar_historial_sensores", HISTORIAN | {"desde": DESDE.replace(tzinfo=None)}),
    ("consultar_historial_sensores", HISTORIAN | {"hasta": "2026-11-20T13:00:00Z"}),
    ("consultar_historial_sensores", HISTORIAN | {"hasta": DESDE + timedelta(days=46)}),
    ("consultar_historial_ordenes", '{"desde":123456}'),
    ("consultar_historial_ordenes", '{"desde":"2026-02-30T13:00:00Z"}'),
    ("consultar_historial_ordenes", {"desde": HASTA, "hasta": DESDE}),
    ("consultar_inventario", {"limite": "1"}), ("consultar_inventario", {"limite": True}),
    ("consultar_stock", {"codigo": "TX-ROD-CIG", "almacen": 1}),
    ("consultar_lista_materiales", {"equipo": "F-201A", "limite": 101}),
    ("consultar_catalogo_tareas", {"clase": False}),
])
def test_argumentos_invalidos_antes_de_http(tool_http, nombre, args):
    tools, requests, _ = tool_http
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar(nombre, args)
    assert error.value.codigo == "argumentos_invalidos" and not requests


@pytest.mark.parametrize("nombre,args", [
    ("consultar_equipos", {"tag": "B-999"}),
    ("consultar_historial_sensores", HISTORIAN | {"variable": "nivel"}),
    ("consultar_historial_ordenes", {"equipo": "B-999"}),
    ("consultar_inventario", {"codigo": "TX-NUEVO"}), ("consultar_stock", {"codigo": "TX-NUEVO"}),
    ("consultar_lista_materiales", {"equipo": "B-999"}), ("consultar_catalogo_tareas", {"codigo": "T-NUEVO"}),
])
def test_identificador_desconocido(tool_http, nombre, args):
    tools, requests, _ = tool_http
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar(nombre, args)
    assert error.value.codigo == "no_encontrado" and not requests


def test_catalogo_schema_campos_extra_y_destinos(tool_http):
    tools, requests, _ = tool_http
    catalogo = tools.catalogo()
    assert len(catalogo) == 7
    for entrada in catalogo:
        assert entrada["descripcion"] and entrada["entrada"]["additionalProperties"] is False
        assert entrada["salida"]["additionalProperties"] is False
        with pytest.raises(ErrorTool) as error:
            tools.ejecutar(entrada["nombre"], {"url": "http://externo/api"})
        assert error.value.codigo == "argumentos_invalidos"
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar("aprobar_orden", {})
    assert error.value.codigo == "tool_desconocida" and not requests
    with pytest.raises(ValueError):
        CatalogoToolsPlanta("http://sim:8100/api")
    with pytest.raises(ValidationError):
        Equipo.model_validate(EQUIPO | {"manometro_local": {"unidad": "psi", "desconocido": True}, "sensores": []})


@pytest.mark.parametrize("nombre,args", [
    ("consultar_historial_sensores", HISTORIAN),
    ("consultar_historial_ordenes", {"equipo": "PLANTA"}),
])
def test_serie_o_historial_vacio_es_exito(tool_http, nombre, args):
    tools, requests, resultados = tool_http
    resultados.append(httpx.Response(200, json=[]))
    datos = tools.ejecutar(nombre, args)
    assert datos["items"] == [] and datos["total"] == 0 and not datos["truncado"]
    assert len(requests) == 1


def test_equipo_ausente_y_filtros_sin_coincidencias(tool_http):
    tools, requests, resultados = tool_http
    catalogo = {"equipos": [EQUIPO], "sensores": [SENSOR]}
    resultados.extend(httpx.Response(200, json=catalogo) for _ in range(2))
    assert tools.ejecutar("consultar_equipos", {"tipo": "filtro"})["total"] == 0
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar("consultar_equipos", {"tag": "B-101"})
    assert error.value.codigo == "no_encontrado" and len(requests) == 2


@pytest.mark.parametrize("nombre,args,salida", [
    ("consultar_equipos", {}, {"equipos": [EQUIPO], "sensores": [SENSOR | {"unidad": 1}]}),
    ("consultar_historial_sensores", HISTORIAN, [{"ts": "2026-11-20T09:00:00-03:00", "valor": True}]),
    ("consultar_historial_sensores", HISTORIAN, [{"ts": "2026-11-20T09:00:00", "valor": 1}]),
    ("consultar_historial_sensores", HISTORIAN, [{"ts": "2026-11-20T11:00:00-03:00", "valor": 1}]),
    ("consultar_historial_sensores", HISTORIAN | {"limite": 1}, [
        {"ts": "2026-11-20T09:00:00-03:00", "valor": 1}, {"ts": "invalida", "valor": 2},
    ]),
    ("consultar_historial_ordenes", {}, [{"id": "OT-X", "fecha": "2026-11-20T09:00:00-03:00"}]),
    ("consultar_historial_ordenes", {"equipo": "B-101"}, [
        {"id": "OT-X", "fecha": "2026-11-20T09:00:00-03:00", "equipo": "B-102", "estado": "Cerrada",
         "tareas": [], "ejecutor": "propio"},
    ]),
])
def test_respuesta_malformada_no_se_oculta_ni_reintenta(tool_http, nombre, args, salida):
    tools, requests, resultados = tool_http
    resultados.append(httpx.Response(200, json=salida))
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar(nombre, args)
    assert error.value.codigo == "respuesta_invalida" and len(requests) == 1


@pytest.mark.parametrize("estado", [301, 401, 404, 422, 500])
def test_http_permanente_y_redireccion_sin_reintentos(tool_http, estado):
    tools, requests, resultados = tool_http
    resultados.append(httpx.Response(estado, headers={"location": "http://otro-host/privado"}))
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar("consultar_historial_sensores", HISTORIAN)
    assert error.value.codigo == "fuente_no_disponible" and str(estado) in str(error.value)
    assert len(requests) == 1 and requests[0].url.host == "sim"


@pytest.mark.parametrize("primero", [503, httpx.ConnectTimeout("cadena sensible"), httpx.ReadError("no disponible")])
def test_reintento_transitorio_unico(tool_http, primero):
    tools, requests, resultados = tool_http
    resultados.extend([httpx.Response(primero) if isinstance(primero, int) else primero, httpx.Response(200, json=[])])
    assert tools.ejecutar("consultar_historial_sensores", HISTORIAN)["items"] == []
    assert len(requests) == 2 and requests[0].url == requests[1].url
    assert requests[0].extensions["timeout"]["read"] == 2


def test_agotamiento_no_encadena_reintentos_ni_expone_error(tool_http):
    tools, requests, resultados = tool_http
    resultados.extend([httpx.ReadTimeout("cadena sensible"), httpx.ReadTimeout("cadena sensible")])
    with pytest.raises(ErrorTool) as error:
        tools.ejecutar("consultar_historial_sensores", HISTORIAN)
    assert error.value.codigo == "fuente_no_disponible" and "cadena sensible" not in str(error.value)
    assert len(requests) == 2


def test_plazo_total_cancela_el_transporte(monkeypatch):
    async def responder(_):
        await asyncio.sleep(0.5)
        return httpx.Response(200, json=[])

    monkeypatch.setattr("sistema.plant_tool_http.PLAZO_HTTP", 0.02)
    tools = CatalogoToolsPlanta(transport=httpx.MockTransport(responder))
    with pytest.raises(ErrorTool, match="Plazo"):
        tools.ejecutar("consultar_historial_ordenes", {})


def test_limite_de_bytes_http_y_json_corrupto(tool_http, monkeypatch):
    tools, requests, resultados = tool_http
    monkeypatch.setattr("sistema.plant_tool_http.MAX_BYTES_HTTP", 20)
    resultados.extend([
        httpx.Response(200, headers={"Content-Type": "application/json", "Content-Length": "21"}, content=b"[]"),
        httpx.Response(200, headers={"Content-Type": "application/json", "Content-Length": "2"}, content=b"0" * 21),
        httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"[?")
    ])
    for codigo in ("limite_excedido", "limite_excedido", "respuesta_invalida"):
        with pytest.raises(ErrorTool) as error:
            tools.ejecutar("consultar_historial_ordenes", {})
        assert error.value.codigo == codigo
    assert len(requests) == 3


@pytest.mark.parametrize("texto,codigo", [
    (None, "fuente_no_disponible"), ("no es json", "respuesta_invalida"),
    ('{"version": 2}', "version_no_compatible"), ('{"version": 1}', "respuesta_invalida"),
])
def test_recurso_ausente_corrupto_o_incompatible(tmp_path, monkeypatch, texto, codigo):
    monkeypatch.setattr("sistema.plant_tools.files", lambda _: tmp_path)
    if texto is not None:
        directorio = tmp_path / "recursos"
        directorio.mkdir()
        (directorio / "catalogo_planta.json").write_text(texto, encoding="utf-8")
    with pytest.raises(ErrorTool) as error:
        cargar_referencia()
    assert error.value.codigo == codigo
