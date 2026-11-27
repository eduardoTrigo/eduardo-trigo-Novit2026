"""Contratos de entrada, resultados y recurso oficial para las tools de planta."""

import re
from datetime import datetime, timedelta
from typing import Annotated, Generic, Literal, TypeVar

from planta_contrato import EstadoOrden, Repuesto
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, FiniteFloat, ValidationInfo, model_validator


Codigo = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")]
Variable = Annotated[str, Field(max_length=64, pattern=r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")]
Texto = Annotated[str, Field(max_length=4096)]
Unidad = Annotated[str, Field(min_length=1, max_length=40)]
TipoEquipo = Literal["bomba", "filtro", "tanque", "caudalimetro"]
ClaseTarea = Literal["estandar", "no_estandar"]
Almacen = Literal["yacimiento", "central"]
ShaGit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NoNegativo = Annotated[FiniteFloat, Field(ge=0)]
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})")


def _fecha_json(valor, info: ValidationInfo):
    if info.mode == "json":
        if not isinstance(valor, str) or _ISO.fullmatch(valor) is None:
            raise ValueError("La fecha debe ser ISO 8601 con zona horaria")
        return datetime.fromisoformat(valor)
    return valor


FechaEntrada = Annotated[AwareDatetime, BeforeValidator(_fecha_json)]


class ErrorTool(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo


class Estricto(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class ConsultaPaginada(Estricto):
    limite: int = Field(default=25, ge=1, le=100)


class ConsultaEquipos(ConsultaPaginada):
    tag: Codigo | None = None
    tipo: TipoEquipo | None = None


class ConsultaHistorialSensores(Estricto):
    tag: Codigo
    variable: Variable
    desde: FechaEntrada
    hasta: FechaEntrada
    limite: int = Field(default=60, ge=1, le=1000)

    @model_validator(mode="after")
    def intervalo_valido(self):
        if not timedelta() <= self.hasta - self.desde <= timedelta(days=45):
            raise ValueError("El intervalo debe ser ascendente y de hasta 45 días")
        return self


class ConsultaHistorialOrdenes(ConsultaPaginada):
    equipo: Codigo | None = None
    desde: FechaEntrada | None = None
    hasta: FechaEntrada | None = None

    @model_validator(mode="after")
    def intervalo_valido(self):
        if self.desde is not None and self.hasta is not None and self.desde > self.hasta:
            raise ValueError("El intervalo debe ser ascendente")
        return self


class ConsultaInventario(ConsultaPaginada):
    codigo: Codigo | None = None


class ConsultaStock(Estricto):
    codigo: Codigo
    almacen: Almacen | None = None


class ConsultaListaMateriales(ConsultaPaginada):
    equipo: Codigo


class ConsultaTareas(ConsultaPaginada):
    codigo: Codigo | None = None
    equipo: Codigo | None = None
    clase: ClaseTarea | None = None


class Manometro(Estricto):
    unidad: Unidad


class Sensor(Estricto):
    tag: Codigo
    variable: Variable
    unidad: Unidad
    alarma_baja: FiniteFloat | None = None
    alarma_alta: FiniteFloat | None = None
    disparo: FiniteFloat | None = None


class EquipoDefinido(Estricto):
    tag: Codigo
    tipo: TipoEquipo
    descripcion: Texto
    nombres: list[Texto]
    fabricante: Texto | None = None
    modelo: Texto | None = None
    caudal_nominal_m3d: NoNegativo | None = None
    manometro_local: Manometro | None = None
    rol_inicial: Texto | None = None
    cartuchos: Annotated[int, Field(ge=0)] | None = None
    altura_m: NoNegativo | None = None
    diametro_m: NoNegativo | None = None


class Equipo(EquipoDefinido):
    sensores: list[Sensor]


class Muestra(Estricto):
    ts: AwareDatetime
    valor: FiniteFloat | None


class CantidadRepuesto(Repuesto):
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    codigo: Codigo
    cantidad: NoNegativo


class Orden(Estricto):
    id: Codigo
    equipo: Codigo
    fecha: AwareDatetime
    estado: EstadoOrden
    ejecutor: Texto
    tareas: list[Codigo]
    origen: Literal["archivo", "simulador"] | None = None
    tipo: Texto | None = None
    fecha_aviso: AwareDatetime | None = None
    fecha_inicio: AwareDatetime | None = None
    fecha_cierre: AwareDatetime | None = None
    descripcion: Texto | None = None
    aviso: Texto | None = None
    trabajo_realizado: Texto | None = None
    repuestos: list[CantidadRepuesto] | None = None
    horas: NoNegativo | None = None
    codigo_falla: Codigo | None = None


class Material(Estricto):
    codigo: Codigo
    nombre: Texto
    unidad: Unidad
    costo_usd: NoNegativo


class MaterialConStock(Material):
    stock: dict[Almacen, Annotated[int, Field(ge=0)]]


class TareaDefinida(Estricto):
    codigo: Codigo
    nombre: Texto
    detiene: bool | None = None
    horas: NoNegativo | None = None
    ejecutor: Texto | None = None
    equipos: list[Codigo] | None = None
    repuestos: list[CantidadRepuesto] | None = None


class Tarea(TareaDefinida):
    clase: ClaseTarea


T = TypeVar("T")


class Pagina(Estricto, Generic[T]):
    items: list[T]
    total: int
    truncado: bool


class HistorialSensores(Pagina[Muestra]):
    tag: Codigo
    variable: Variable
    unidad: Unidad
    desde: AwareDatetime
    hasta: AwareDatetime


class PaginaReferencia(Pagina[T], Generic[T]):
    origen: Literal["referencia"]
    sha: ShaGit


class Stock(MaterialConStock):
    origen: Literal["referencia"]
    sha: ShaGit


class ListaMateriales(PaginaReferencia[Material]):
    equipo: Codigo
    estado: Literal["definida", "sin_lista"]


class ReferenciaPlanta(Estricto):
    version: Literal[1]
    sha: ShaGit
    archivos: dict[str, Sha256]
    equipos: dict[Codigo, dict[Variable, Unidad]]
    equipos_archivo_ordenes: list[Codigo]
    repuestos: list[MaterialConStock]
    lista_materiales: dict[Codigo, list[Codigo]]
    tareas_estandar: list[TareaDefinida]
    tareas_no_estandar: list[TareaDefinida]

    @model_validator(mode="after")
    def referencias_validas(self):
        codigos = {r.codigo for r in self.repuestos}
        if not self.equipos or len(codigos) != len(self.repuestos) or not self.repuestos:
            raise ValueError("Identidades de equipos o repuestos inválidas")
        for equipo, repuestos in self.lista_materiales.items():
            if equipo not in self.equipos or not set(repuestos) <= codigos:
                raise ValueError("Lista con referencias inexistentes")
        for tarea in (*self.tareas_estandar, *self.tareas_no_estandar):
            if not set(tarea.equipos or ()) <= self.equipos.keys() or any(
                r.codigo not in codigos for r in tarea.repuestos or ()
            ):
                raise ValueError("Tarea con referencias inexistentes")
        return self
