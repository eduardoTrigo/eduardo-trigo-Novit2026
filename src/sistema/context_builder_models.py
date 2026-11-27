"""Contratos del ensamblado de contexto y de sus entradas de solo lectura."""

from math import isfinite
from typing import Annotated, Generic, Literal, TypeVar

from planta_contrato import FilaParte, NotaRecorrida
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, model_validator

from sistema.plant_tool_models import (
    Codigo, Equipo, HistorialSensores, ListaMateriales, Material, Orden, Pagina, PaginaReferencia,
    Sha256, ShaGit, Stock, Tarea, Unidad, Variable,
)
from sistema.rag_retrieval import ResultadoManual, ResultadoManuales


class ContratoContexto(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True)


Identidad = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
Positivo = Annotated[int, Field(gt=0)]
Conteo = Annotated[int, Field(ge=0)]
TareaContexto = Literal["parte", "aclaracion", "documentacion", "materiales"]
Tool = Literal[
    "consultar_equipos", "consultar_historial_sensores", "consultar_historial_ordenes", "consultar_inventario",
    "consultar_stock", "consultar_lista_materiales", "consultar_catalogo_tareas", "buscar_manuales",
]
Fuente = Tool | Literal["memoria_sqlite", "memoria_postgres"]
ToolPaginada = Literal[
    "consultar_equipos", "consultar_historial_sensores", "consultar_historial_ordenes",
    "consultar_inventario", "consultar_lista_materiales", "consultar_catalogo_tareas",
]
TipoLimite = Literal["alarma_baja", "alarma_alta", "disparo"]
EstadoDato = Literal[
    "seleccionada", "vacia", "faltante", "no_disponible", "tiempo_agotado", "invalida", "sensible",
    "obsoleta", "indeterminada", "contradictoria", "fuera_de_alcance", "no_aplicable", "duplicado",
    "compactado", "omitido_presupuesto", "truncada_origen", "sin_coincidencias",
]
Sincronizacion = Literal["sincronizada", "pendiente", "desalineada", "sin_referencia_local", "no_encontrada"]


class NotaContexto(ContratoContexto):
    id: Identidad
    ts: AwareDatetime
    texto: Annotated[str, Field(min_length=1, max_length=32000, pattern=r"\S")]

    @classmethod
    def desde_recorrida(cls, nota: NotaRecorrida, texto: str) -> "NotaContexto":
        return cls(id=nota.id, ts=nota.ts, texto=texto)


class Alcance(ContratoContexto):
    equipos: Annotated[tuple[Codigo, ...], Field(max_length=16)] = ()
    variables: Annotated[tuple[Variable, ...], Field(max_length=16)] = ()
    codigos: Annotated[tuple[Codigo, ...], Field(max_length=16)] = ()
    incluir_ordenes: bool = False
    max_antiguedad_muestra_s: Annotated[int, Field(ge=1, le=86400)] = 600


class Presupuesto(ContratoContexto):
    entrada: Positivo = 4200
    reserva_salida: Positivo = 900
    ventana: Positivo | None = None
    max_bytes: Positivo = 20000
    tope_tools: Positivo = 1400
    tope_documentos: Positivo = 1800
    tope_memoria: Positivo = 1600

    @property
    def disponible(self) -> int:
        if self.ventana is None:
            return self.entrada
        return max(0, min(self.entrada, 3 * self.ventana // 4 - self.reserva_salida))


T = TypeVar("T")


class Lectura(ContratoContexto, Generic[T]):
    obtenida_en: AwareDatetime
    dato: T


class FalloFuente(ContratoContexto):
    fuente: Fuente
    estado: Literal["no_disponible", "tiempo_agotado", "invalida"]


class LimiteDocumental(ContratoContexto):
    cita: Annotated[str, Field(min_length=1, max_length=160)]
    tag: Codigo
    variable: Variable
    tipo: TipoLimite
    unidad: Unidad
    valor: FiniteFloat


class DatosContexto(ContratoContexto):
    equipos: Lectura[Pagina[Equipo]] | None = None
    historian: Annotated[tuple[Lectura[HistorialSensores], ...], Field(max_length=32)] = ()
    ordenes: Lectura[Pagina[Orden]] | None = None
    inventario: Lectura[PaginaReferencia[Material]] | None = None
    stock: Annotated[tuple[Lectura[Stock], ...], Field(max_length=32)] = ()
    materiales: Annotated[tuple[Lectura[ListaMateriales], ...], Field(max_length=32)] = ()
    tareas: Lectura[PaginaReferencia[Tarea]] | None = None
    documentos: Lectura[ResultadoManuales] | None = None
    limites_documentales: Annotated[tuple[LimiteDocumental, ...], Field(max_length=32)] = ()
    fallos: Annotated[tuple[FalloFuente, ...], Field(max_length=32)] = ()


class VersionMemoria(ContratoContexto):
    version: Annotated[int, Field(ge=0)]
    mensaje_id: Identidad
    filas: tuple[FilaParte, ...]
    texto: str | None = None
    espera_respuesta: bool | None = None
    filas_repetidas_de: Identidad | None = None


class RespuestaMemoria(ContratoContexto):
    id: Identidad
    mensaje_id: Identidad
    ts: AwareDatetime
    resultado: str | None
    texto: str | None
    texto_stt: str | None = None


class MemoriaNota(ContratoContexto):
    nota_id: Identidad
    ts_nota: AwareDatetime
    revision: Positivo
    actualizada_en: AwareDatetime
    resuelta_en: AwareDatetime | None = None
    estado_parte: str | None = None
    version_actual: Annotated[int, Field(ge=0)] | None = None
    mensaje_actual: Identidad | None = None
    filas: tuple[FilaParte, ...] = ()
    versiones: tuple[VersionMemoria, ...] = ()
    respuestas: tuple[RespuestaMemoria, ...] = ()

    @model_validator(mode="after")
    def validar_dialogo(self):
        mensajes = {b.mensaje_id for b in self.versiones}
        if any(r.mensaje_id not in mensajes for r in self.respuestas):
            raise ValueError("La respuesta no pertenece a un borrador de la nota")
        for filas in (self.filas, *(b.filas for b in self.versiones)):
            if any(f.valor is not None and not isfinite(f.valor) for f in filas):
                raise ValueError("La memoria contiene un valor no finito")
        return self


class LecturaMemoria(ContratoContexto):
    revision_sqlite: Positivo | None = None
    local: MemoriaNota | None = None
    postgres: MemoriaNota | None = None


class EntradaContexto(ContratoContexto):
    nota: NotaContexto
    tarea: TareaContexto
    evaluado_en: AwareDatetime
    alcance: Alcance = Field(default_factory=Alcance)
    datos: DatosContexto = Field(default_factory=DatosContexto)
    memoria: LecturaMemoria = Field(default_factory=LecturaMemoria)
    presupuesto: Presupuesto = Field(default_factory=Presupuesto)

    @model_validator(mode="after")
    def orden_temporal(self):
        if self.evaluado_en < self.nota.ts:
            raise ValueError("La referencia de evaluación precede a la nota")
        return self


class DocumentoContexto(ContratoContexto):
    fuente: Literal["buscar_manuales"] = "buscar_manuales"
    obtenida_en: AwareDatetime
    corpus: Sha256
    modo: Literal["textual_postgresql"]
    resultado: ResultadoManual


class MemoriaContexto(ContratoContexto):
    sincronizacion: Sincronizacion
    revision_sqlite: Positivo | None = None
    revision_postgres: Positivo | None = None
    origen: Literal["memoria_sqlite", "memoria_postgres"] | None = None
    datos: MemoriaNota | None = None


class CoberturaHistorian(ContratoContexto):
    tag: Codigo
    variable: Variable
    unidad: Unidad
    desde: AwareDatetime
    hasta: AwareDatetime
    primera_recibida: AwareDatetime | None
    ultima_recibida: AwareDatetime | None


class PaginaContexto(ContratoContexto):
    id: Positivo
    fuente: ToolPaginada
    obtenida_en: AwareDatetime
    total: Conteo
    recibidos: Conteo
    truncado: bool
    cobertura_historian: CoberturaHistorian | None = None
    equipo: Codigo | None = None
    origen: Literal["referencia"] | None = None
    sha: ShaGit | None = None


class ResultadoTool(ContratoContexto):
    fuente: Tool
    obtenida_en: AwareDatetime
    naturaleza: Literal["definicion_oficial", "medicion_historian", "antecedente", "referencia"]
    datos: dict[str, JsonValue]
    paginas: tuple[Positivo, ...] = ()


class Contradiccion(ContratoContexto):
    estado: Literal["contradictoria", "indeterminada"]
    fuente_oficial: Literal["consultar_equipos"] = "consultar_equipos"
    obtenida_en: AwareDatetime
    corpus: Sha256
    valor_oficial: FiniteFloat
    unidad_oficial: Unidad
    documento: LimiteDocumental


class DecisionContexto(ContratoContexto):
    fuente: Fuente
    estado: EstadoDato
    cantidad: Positivo


class EntradaActual(ContratoContexto):
    tarea: TareaContexto
    nota: NotaContexto


class ContratoTool(ContratoContexto):
    nombre: Tool
    parametros: tuple[str, ...]
    requeridos: tuple[str, ...]


class ContextoModelo(ContratoContexto):
    instrucciones_confiables: tuple[str, ...]
    contratos_tools: tuple[ContratoTool, ...]
    documentos: tuple[DocumentoContexto, ...]
    memoria: MemoriaContexto
    paginas_fuente: tuple[PaginaContexto, ...] = ()
    resultados_tools: tuple[ResultadoTool, ...]
    contradicciones: tuple[Contradiccion, ...]
    estado_fuentes: tuple[DecisionContexto, ...]
    entrada_actual: EntradaActual


class UsoContexto(ContratoContexto):
    aproximado: bool
    tokens: int
    bytes_serializados: int
    entrada_disponible: int
    reserva_salida: int


class ResultadoContexto(ContratoContexto):
    contexto: ContextoModelo
    decisiones: tuple[DecisionContexto, ...]
    uso: UsoContexto

    def serializar_contexto(self) -> str:
        return self.contexto.model_dump_json(exclude_none=False)
