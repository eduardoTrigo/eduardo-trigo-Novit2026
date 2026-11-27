"""Catálogo explícito de consultas de planta para futuros adaptadores."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import cached_property
from importlib.resources import files
from typing import NamedTuple

import httpx
from planta_contrato import EstadoOrden
from pydantic import ValidationError

from sistema.part_context import ClientePlanta
from sistema.plant_tool_http import LectorPlanta
from sistema.plant_tool_models import (
    ConsultaEquipos, ConsultaHistorialOrdenes, ConsultaHistorialSensores, ConsultaInventario,
    ConsultaListaMateriales, ConsultaStock, ConsultaTareas, Equipo, EquipoDefinido, ErrorTool,
    Estricto, HistorialSensores, ListaMateriales, Material, Muestra, Orden, Pagina, PaginaReferencia,
    ReferenciaPlanta, Sensor, Stock, Tarea,
)
from sistema.rag_retrieval import BuscadorDocumental, ConsultaManuales, RecuperadorPostgres, ResultadoManuales


_ZONA_HISTORIAN = timezone(timedelta(hours=-3))
_MAX_BYTES_REFERENCIA = 256 * 1024
_MAX_BYTES_ARGUMENTOS = 16 * 1024


def cargar_referencia() -> ReferenciaPlanta:
    try:
        recurso = files("sistema").joinpath("recursos", "catalogo_planta.json")
        with recurso.open("rb") as archivo:
            contenido = archivo.read(_MAX_BYTES_REFERENCIA + 1)
    except OSError:
        raise ErrorTool("fuente_no_disponible", "Catálogo oficial empaquetado inaccesible") from None
    if len(contenido) > _MAX_BYTES_REFERENCIA:
        raise ErrorTool("limite_excedido", "El catálogo oficial supera el límite de tamaño")
    try:
        datos = json.loads(contenido)
        if not isinstance(datos, dict) or type(datos.get("version")) is not int:
            raise ValueError("Versión ausente o inválida")
    except (ValueError, UnicodeError, RecursionError):
        raise ErrorTool("respuesta_invalida", "Catálogo oficial empaquetado inválido") from None
    if datos["version"] != 1:
        raise ErrorTool("version_no_compatible", "Versión del catálogo oficial incompatible")
    try:
        return ReferenciaPlanta.model_validate(datos)
    except ValidationError:
        raise ErrorTool("respuesta_invalida", "Catálogo oficial empaquetado inválido") from None


def paginar_resultado(items: list, limite: int) -> dict:
    return {"items": items[:limite], "total": len(items), "truncado": len(items) > limite}


def _fecha(fuente) -> datetime:
    if not isinstance(fuente, str):
        raise ValueError("Fecha de la fuente inválida")
    fecha = datetime.fromisoformat(fuente)
    if fecha.utcoffset() is None:
        raise ValueError("Fecha de la fuente sin zona")
    return fecha


def _orden(datos: dict) -> Orden:
    if not isinstance(datos, dict):
        raise ValueError("Orden inválida")
    # El archivo incluye datos personales que esta consulta no necesita.
    campos = {k: v for k, v in datos.items() if k not in {"solicitante", "tecnico"}}
    for campo in ("fecha", "fecha_aviso", "fecha_inicio", "fecha_cierre"):
        if campos.get(campo) is not None:
            campos[campo] = _fecha(campos[campo])
    campos["estado"] = EstadoOrden(datos.get("estado"))
    return Orden.model_validate(campos)


class _Tool(NamedTuple):
    descripcion: str
    entrada: type[Estricto]
    salida: type[Estricto]
    ejecutar: Callable


class CatalogoToolsPlanta:
    def __init__(
        self, destino: str = "http://sim:8100", *, transport: httpx.AsyncBaseTransport | None = None,
        retriever: BuscadorDocumental | None = None,
    ):
        self._destino = destino
        self._leer_http = LectorPlanta(destino, transport)
        self._retriever = retriever
        self._tools = {
            "consultar_equipos": _Tool("Consulta equipos, aliases y sensores de planta.",
                                      ConsultaEquipos, Pagina[Equipo], self._equipos),
            "consultar_historial_sensores": _Tool("Consulta un sensor oficial en un intervalo de hasta 45 días.",
                                                 ConsultaHistorialSensores, HistorialSensores, self._historial),
            "consultar_historial_ordenes": _Tool("Consulta órdenes históricas por equipo y período.",
                                                ConsultaHistorialOrdenes, Pagina[Orden], self._ordenes),
            "consultar_inventario": _Tool("Consulta el inventario oficial de referencia.",
                                         ConsultaInventario, PaginaReferencia[Material], self._inventario),
            "consultar_stock": _Tool("Consulta cantidades de referencia por almacén.",
                                    ConsultaStock, Stock, self._stock),
            "consultar_lista_materiales": _Tool("Consulta repuestos de referencia asociados a un equipo.",
                                                ConsultaListaMateriales, ListaMateriales, self._materiales),
            "consultar_catalogo_tareas": _Tool("Consulta tareas estándar y no estándar y su aplicabilidad.",
                                              ConsultaTareas, PaginaReferencia[Tarea], self._tareas),
            "buscar_manuales": _Tool(
                "Busca citas en documentos y órdenes de archivo. Para límites operativos o cantidades de stock "
                "consulta las fuentes de datos de planta.",
                ConsultaManuales, ResultadoManuales, self._manuales,
            ),
        }

    def _manuales(self, consulta: ConsultaManuales) -> ResultadoManuales:
        buscador = self._retriever if self._retriever is not None else RecuperadorPostgres.desde_entorno()
        return buscador.buscar(consulta)

    @cached_property
    def _referencia(self) -> ReferenciaPlanta:
        return cargar_referencia()

    def catalogo(self) -> list[dict]:
        return [
            {"nombre": nombre, "descripcion": tool.descripcion, "entrada": tool.entrada.model_json_schema(),
             "salida": tool.salida.model_json_schema()}
            for nombre, tool in self._tools.items()
        ]

    def ejecutar(self, nombre: str, argumentos: dict | str | bytes) -> dict:
        """En un consumidor asíncrono, invocar este método con asyncio.to_thread."""
        if not isinstance(nombre, str) or nombre not in self._tools:
            raise ErrorTool("tool_desconocida", "Tool de planta no autorizada")
        tool = self._tools[nombre]
        try:
            if isinstance(argumentos, (str, bytes)):
                if len(argumentos) > _MAX_BYTES_ARGUMENTOS or (
                    isinstance(argumentos, str) and len(argumentos.encode("utf-8")) > _MAX_BYTES_ARGUMENTOS
                ):
                    raise ErrorTool("limite_excedido", "Argumentos JSON demasiado grandes")
                consulta = tool.entrada.model_validate_json(argumentos)
            else:
                if not isinstance(argumentos, dict):
                    raise ValueError("Se requiere un objeto")
                consulta = tool.entrada.model_validate(argumentos)
        except (ValueError, OverflowError):
            raise ErrorTool("argumentos_invalidos", "Argumentos incompatibles con el contrato de la tool") from None
        try:
            resultado = tool.ejecutar(consulta)
        except (ValueError, TypeError, OverflowError):
            raise ErrorTool("respuesta_invalida", "La fuente no cumple el contrato de salida") from None
        return resultado.model_dump(mode="json", exclude_unset=True)

    def _cliente(self) -> ClientePlanta:
        return ClientePlanta(self._destino, leer_json=self._leer_http)

    def _validar_equipo(self, equipo: str) -> None:
        if equipo not in self._referencia.equipos:
            raise ErrorTool("no_encontrado", "Equipo inexistente en la definición oficial")

    def _equipos(self, consulta: ConsultaEquipos) -> Pagina[Equipo]:
        if consulta.tag is not None:
            self._validar_equipo(consulta.tag)
        datos = self._cliente().catalogo(requerir_sensores=True)
        equipos = [EquipoDefinido.model_validate(e) for e in datos["equipos"]]
        tags = {e.tag for e in equipos}
        sensores = [Sensor.model_validate(s) for s in datos["sensores"]]
        if len(tags) != len(equipos) or len({(s.tag, s.variable) for s in sensores}) != len(sensores) or any(
            s.tag not in tags for s in sensores
        ):
            raise ValueError("Catálogo de planta inconsistente")
        if consulta.tag is not None and consulta.tag not in tags:
            raise ErrorTool("no_encontrado", "Equipo ausente del catálogo de planta")
        items = [
            Equipo(**e.model_dump(exclude_unset=True), sensores=[s for s in sensores if s.tag == e.tag])
            for e in equipos if (consulta.tag is None or e.tag == consulta.tag)
            and (consulta.tipo is None or e.tipo == consulta.tipo)
        ]
        return Pagina[Equipo](**paginar_resultado(items, consulta.limite))

    def _historial(self, consulta: ConsultaHistorialSensores) -> HistorialSensores:
        self._validar_equipo(consulta.tag)
        unidad = self._referencia.equipos[consulta.tag].get(consulta.variable)
        if unidad is None:
            raise ErrorTool("no_encontrado", "Variable inexistente para el equipo consultado")
        try:
            desde = consulta.desde.astimezone(_ZONA_HISTORIAN)
            hasta = consulta.hasta.astimezone(_ZONA_HISTORIAN)
        except OverflowError:
            raise ErrorTool("argumentos_invalidos", "Fecha fuera de rango para el historian") from None
        datos = self._cliente().historial(consulta.tag, consulta.variable, hasta, desde=desde)
        if len(datos) > 64_800:
            raise ErrorTool("limite_excedido", "La fuente excedió las filas del historian")
        items = []
        for fila in datos:
            if not isinstance(fila, dict):
                raise ValueError("Muestra inválida")
            muestra = Muestra.model_validate(fila | {"ts": _fecha(fila.get("ts"))})
            if not desde <= muestra.ts <= hasta:
                raise ValueError("Muestra fuera de intervalo")
            items.append(muestra)
        items.sort(key=lambda muestra: muestra.ts)
        return HistorialSensores(tag=consulta.tag, variable=consulta.variable, unidad=unidad, desde=desde, hasta=hasta,
                                **paginar_resultado(items, consulta.limite))

    def _ordenes(self, consulta: ConsultaHistorialOrdenes) -> Pagina[Orden]:
        if consulta.equipo is not None and consulta.equipo not in self._referencia.equipos_archivo_ordenes:
            self._validar_equipo(consulta.equipo)
        datos = self._cliente().historial_ordenes(equipo=consulta.equipo, desde=consulta.desde, hasta=consulta.hasta)
        if len(datos) > 10_000:
            raise ErrorTool("limite_excedido", "La fuente excedió las filas del historial de órdenes")
        items = [_orden(o) for o in datos]
        if any((consulta.equipo is not None and o.equipo != consulta.equipo)
               or (consulta.desde is not None and o.fecha < consulta.desde)
               or (consulta.hasta is not None and o.fecha > consulta.hasta) for o in items):
            raise ValueError("Orden fuera de los filtros enviados")
        items.sort(key=lambda orden: orden.fecha, reverse=True)
        return Pagina[Orden](**paginar_resultado(items, consulta.limite))

    def _procedencia(self) -> dict:
        return {"origen": "referencia", "sha": self._referencia.sha}

    def _repuesto(self, codigo: str):
        item = next((r for r in self._referencia.repuestos if r.codigo == codigo), None)
        if item is None:
            raise ErrorTool("no_encontrado", "Código de material inexistente")
        return item

    def _inventario(self, consulta: ConsultaInventario) -> PaginaReferencia[Material]:
        items = [self._repuesto(consulta.codigo)] if consulta.codigo else self._referencia.repuestos
        return PaginaReferencia[Material](**paginar_resultado(items, consulta.limite), **self._procedencia())

    def _stock(self, consulta: ConsultaStock) -> Stock:
        material = self._repuesto(consulta.codigo)
        stock = material.stock
        if consulta.almacen is not None:
            if consulta.almacen not in stock:
                raise ErrorTool("no_encontrado", "El catálogo no informa stock para ese almacén")
            stock = {consulta.almacen: stock[consulta.almacen]}
        return Stock(**material.model_dump(exclude={"stock"}), stock=stock, **self._procedencia())

    def _materiales(self, consulta: ConsultaListaMateriales) -> ListaMateriales:
        self._validar_equipo(consulta.equipo)
        lista = self._referencia.lista_materiales.get(consulta.equipo)
        items = [self._repuesto(codigo) for codigo in lista or ()]
        return ListaMateriales(equipo=consulta.equipo, estado="definida" if lista is not None else "sin_lista",
                              **paginar_resultado(items, consulta.limite), **self._procedencia())

    def _tareas(self, consulta: ConsultaTareas) -> PaginaReferencia[Tarea]:
        if consulta.equipo is not None:
            self._validar_equipo(consulta.equipo)
        catalogo = [("estandar", self._referencia.tareas_estandar), ("no_estandar", self._referencia.tareas_no_estandar)]
        if consulta.codigo is not None and not any(t.codigo == consulta.codigo for _, tareas in catalogo for t in tareas):
            raise ErrorTool("no_encontrado", "Código de tarea inexistente")
        items = [
            Tarea(**tarea.model_dump(exclude_unset=True), clase=clase)
            for clase, tareas in catalogo if consulta.clase is None or consulta.clase == clase
            for tarea in tareas if (consulta.codigo is None or consulta.codigo == tarea.codigo)
            and (consulta.equipo is None or consulta.equipo in (tarea.equipos or ()))
        ]
        return PaginaReferencia[Tarea](**paginar_resultado(items, consulta.limite), **self._procedencia())
