"""Lectura y preparación del corpus documental oficial de PIA-3."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Annotated, Literal
from zipfile import BadZipFile, ZipFile

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


SHA_SIMULADOR = "ab65b95e911731351dba395817a74a8cc60da2b2"
VERSION_FORMATO = 1
VERSION_PREPARACION = 1
MAX_ARCHIVO = 4 * 1024 * 1024
FUENTES = {
    "indice.json": "datos/documentos/indice.json",
    "fragmentos.jsonl": "datos/documentos/fragmentos.jsonl",
    "ordenes.json": "datos/documentos/historial/ordenes.json",
    "equipos.json": "datos/planta.yaml",
}
REFERENCIAS = re.compile(r"\b(?:TX-250|TX-500|FC-24|FC-96|PIA-1|PIA-2)\b")
FALLA = re.compile(r"F(?:0[1-9]|10)")
CODIGO_FABRICANTE = re.compile(r"(?:TX|FC)-T[0-9]{2}")
EVIDENCIA_SECCION = {
    ("TX350-C", "3.3"): ("no_aplicable", "Estos valores no aplican a la TX-350."),
    ("TX350-D", "3.3"): ("no_aplicable", "Estos valores no aplican a la TX-350."),
    ("TX350-C", "1.1"): ("confirmada", "se mencionan solo para comparar."),
    ("TX350-D", "1.1"): ("confirmada", "se mencionan solo para comparar."),
    ("TX350-C", "2.1"): ("confirmada", "Característica | TX-250 | TX-350 | TX-500"),
    ("TX350-D", "2.1"): ("confirmada", "Característica | TX-250 | TX-350 | TX-500"),
    ("FC48-3", "2.1"): ("confirmada", "Este manual trata solo el FC-48."),
    ("MBP-4", "1.1"): ("confirmada", "PIA-3 | 3 triplex TX-350"),
    ("MBP-4", "5.3"): ("no_aplicable", "No aplican a PIA-3."),
    ("CT-SVP-2025", "1.1"): ("confirmada", "PIA-1, PIA-2 y PIA-3."),
}

NoVacio = Annotated[str, Field(pattern=r"\S")]
Positivo = Annotated[int, Field(gt=0)]
Huella = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ErrorCorpus(ValueError):
    """El paquete o sus referencias son inconsistentes."""


class Contrato(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Manifiesto(Contrato):
    formato: Literal[1]
    sha_simulador: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    fuentes: dict[str, Huella]
    archivos: dict[str, Huella]


class SeccionFuente(Contrato):
    id: NoVacio
    numero: Annotated[str, Field(pattern=r"^(?:[0-9]+|[A-Z])(?:\.[0-9]+)*$")]
    titulo: NoVacio
    nivel: Positivo
    clave: str | None
    pagina: Positivo


class DocumentoFuente(Contrato):
    id: NoVacio
    titulo: NoVacio
    subtitulo: NoVacio
    codigo: NoVacio
    revision: NoVacio
    vigente_desde: date
    vigente: bool
    reemplaza: NoVacio | None
    reemplazado_por: NoVacio | None
    tipo: Literal["manual_fabricante", "manual_operadora", "contrato"]
    emisor: NoVacio
    equipos: list[NoVacio]
    archivos: dict[str, NoVacio]
    paginas: Positivo
    secciones: list[SeccionFuente]


class HistorialFuente(Contrato):
    id: Literal["historial"]
    titulo: NoVacio
    tipo: Literal["historial"]
    desde: date
    hasta: date
    ordenes: Positivo
    archivos: dict[str, NoVacio]


class IndiceFuente(Contrato):
    semilla: int
    documentos: list[Annotated[DocumentoFuente | HistorialFuente, Field(discriminator="tipo")]]


class FragmentoFuente(Contrato):
    id: NoVacio
    doc: NoVacio
    numero: NoVacio
    titulo: NoVacio
    clave: str | None = None
    pagina: Positivo | None = None
    vigente: bool | None = None
    texto: NoVacio


class EquipoFuente(Contrato):
    tag: NoVacio
    modelo: NoVacio | None = None
    fabricante: NoVacio | None = None


class OrdenFuente(Contrato):
    id: Annotated[str, Field(pattern=r"^OT-[0-9]{2}-[0-9]{4}$")]
    equipo: NoVacio
    fecha_aviso: AwareDatetime | None = None
    fecha_inicio: AwareDatetime | None = None
    fecha_cierre: AwareDatetime | None = None
    tipo: NoVacio | None = None
    tareas: list[NoVacio] | None = None
    ejecutor: NoVacio | None = None
    codigo_falla: Annotated[str, Field(pattern=r"^F(?:0[1-9]|10)$")] | None = None
    estado: NoVacio | None = None


class Documento(Contrato):
    id: NoVacio
    titulo: NoVacio
    tipo: NoVacio
    emisor: str | None = None
    fabricante: str | None = None
    equipos: tuple[str, ...] = ()
    modelos: tuple[str, ...] = ()
    codigo: str | None = None
    revision: str | None = None
    vigente: bool | None = None
    vigente_desde: date | None = None
    reemplaza: str | None = None
    reemplazado_por: str | None = None
    archivos: dict[str, str]


class Aplicabilidad(Contrato):
    estado: Literal["confirmada", "no_aplicable", "indeterminada"]
    referencias: tuple[str, ...] = ()
    cita: str | None = None
    evidencia: str | None = None


class CodigoFalla(Contrato):
    codigo_fabricante: NoVacio
    falla: NoVacio
    cita: NoVacio


class Chunk(Contrato):
    id: NoVacio
    documento: NoVacio
    titulo: NoVacio
    cita: NoVacio
    seccion: SeccionFuente | None = None
    pagina: int | None = None
    vigente: bool | None = None
    equipos: tuple[str, ...] = ()
    modelos: tuple[str, ...] = ()
    alcance_equipos: Literal["documento", "orden"] | None = None
    aplicabilidad: Aplicabilidad
    orden: OrdenFuente | None = None
    codigos_falla: tuple[CodigoFalla, ...] = ()
    texto: NoVacio
    texto_embedding: NoVacio
    sha_texto: Huella


@dataclass(frozen=True)
class CorpusFuente:
    manifiesto: Manifiesto
    indice: IndiceFuente
    fragmentos: tuple[FragmentoFuente, ...]
    ordenes: tuple[OrdenFuente, ...]
    equipos: tuple[EquipoFuente, ...]


@dataclass(frozen=True)
class CorpusPreparado:
    manifiesto: Manifiesto
    documentos: tuple[Documento, ...]
    chunks: tuple[Chunk, ...]

    @property
    def identidad(self) -> str:
        return identidad_corpus(self.manifiesto.model_dump(mode="json"), VERSION_PREPARACION)


def identidad_corpus(manifiesto: dict, preparacion: int) -> str:
    return huella(json_canonico({"manifiesto": manifiesto, "preparacion": preparacion}))


def huella(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def json_canonico(datos: object) -> bytes:
    return json.dumps(datos, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def leer_corpus(datos: bytes) -> CorpusFuente:
    if len(datos) > MAX_ARCHIVO:
        raise ErrorCorpus("El paquete supera el tamaño admitido")
    try:
        with ZipFile(BytesIO(datos)) as archivo:
            nombres = archivo.namelist()
            if len(nombres) != len(FUENTES) + 1 or set(nombres) != {*FUENTES, "manifiesto.json"}:
                raise ErrorCorpus("El paquete contiene archivos ausentes, duplicados o no admitidos")
            if sum(info.file_size for info in archivo.infolist()) > MAX_ARCHIVO:
                raise ErrorCorpus("El contenido del paquete supera el tamaño admitido")
            manifiesto = Manifiesto.model_validate_json(archivo.read("manifiesto.json"))
            contenido = {nombre: archivo.read(nombre) for nombre in FUENTES}
        if (
            manifiesto.fuentes.keys() != set(FUENTES.values())
            or manifiesto.archivos != {nombre: huella(raw) for nombre, raw in contenido.items()}
            or any(manifiesto.fuentes[FUENTES[n]] != manifiesto.archivos[n] for n in ("indice.json", "fragmentos.jsonl"))
        ):
            raise ErrorCorpus("El manifiesto y los hashes del contenido son inconsistentes")
        return CorpusFuente(
            manifiesto,
            IndiceFuente.model_validate_json(contenido["indice.json"]),
            tuple(FragmentoFuente.model_validate_json(linea) for linea in contenido["fragmentos.jsonl"].splitlines()),
            TypeAdapter(tuple[OrdenFuente, ...]).validate_json(contenido["ordenes.json"]),
            TypeAdapter(tuple[EquipoFuente, ...]).validate_json(contenido["equipos.json"]),
        )
    except ValidationError as error:
        campo = ".".join(map(str, error.errors(include_input=False)[0]["loc"]))
        raise ErrorCorpus(f"Campo inválido en el corpus: {campo or 'raíz'}") from None
    except (BadZipFile, NotImplementedError, RuntimeError, UnicodeError) as error:
        raise ErrorCorpus("Paquete documental inválido") from error


def _unicos(registros, atributo):
    por_id = {}
    for registro in registros:
        clave = getattr(registro, atributo)
        if clave in por_id:
            raise ErrorCorpus(f"Identificador duplicado: {clave}")
        por_id[clave] = registro
    return por_id


def _modelos(equipos, catalogo):
    return tuple(sorted({catalogo[tag].modelo for tag in equipos if catalogo[tag].modelo}))


def _documento(fuente, catalogo):
    if isinstance(fuente, HistorialFuente):
        return Documento(id=fuente.id, titulo=fuente.titulo, tipo=fuente.tipo, archivos=fuente.archivos)
    equipos = tuple(fuente.equipos)
    if len(set(equipos)) != len(equipos) or set(equipos) - catalogo.keys():
        raise ErrorCorpus(f"Equipos inconsistentes en {fuente.id}")
    fabricantes = {catalogo[tag].fabricante for tag in equipos}
    fabricante = next(iter(fabricantes)) if fuente.tipo == "manual_fabricante" and len(fabricantes) == 1 else None
    return Documento(
        **fuente.model_dump(exclude={"subtitulo", "equipos", "secciones", "paginas"}),
        equipos=equipos, modelos=_modelos(equipos, catalogo), fabricante=fabricante,
    )


def _aplicabilidad(fragmento, fragmentos):
    referencias = tuple(sorted(set(REFERENCIAS.findall(fragmento.texto))))
    cita, evidencia = fragmento.id, None
    estado = "indeterminada"
    clave = (fragmento.doc, fragmento.numero)
    if fragmento.doc == "MBP-4" and fragmento.numero.split(".")[0] == "17":
        cita = "MBP-4 §17"
        regla = ("no_aplicable", "Estas secciones no aplican a PIA-3.")
    else:
        regla = EVIDENCIA_SECCION.get(clave)
    if regla:
        origen = fragmentos.get(cita)
        if not origen or regla[1] not in origen.texto:
            return Aplicabilidad(estado="indeterminada", referencias=referencias)
        estado, evidencia = regla
    return Aplicabilidad(estado=estado, referencias=referencias, cita=cita if evidencia else None, evidencia=evidencia)


def correspondencias_fallas(fragmento: FragmentoFuente) -> tuple[CodigoFalla, ...]:
    resultados = []
    for linea in fragmento.texto.splitlines():
        columnas = [columna.strip() for columna in linea.split("|")]
        if not FALLA.fullmatch(columnas[0]):
            continue
        codigos = columnas[2].split(" / ") if len(columnas) == 5 else []
        if not codigos or any(not CODIGO_FABRICANTE.fullmatch(codigo) for codigo in codigos):
            raise ErrorCorpus(f"Código de fabricante inválido en {fragmento.id}")
        resultados.extend(
            CodigoFalla(codigo_fabricante=codigo, falla=columnas[0], cita=fragmento.id) for codigo in codigos
        )
    _unicos(resultados, "codigo_fabricante")
    return tuple(resultados)


def preparar_corpus(corpus: CorpusFuente) -> CorpusPreparado:
    catalogo = _unicos(corpus.equipos, "tag")
    fuentes = _unicos(corpus.indice.documentos, "id")
    documentos = {doc.id: _documento(doc, catalogo) for doc in fuentes.values()}
    fragmentos = _unicos(corpus.fragmentos, "id")
    ordenes = _unicos(corpus.ordenes, "id")
    historial = fuentes.get("historial")
    if not documentos or not isinstance(historial, HistorialFuente) or historial.ordenes != len(ordenes):
        raise ErrorCorpus("Cantidad de órdenes inconsistente con el índice oficial")
    if any(o.equipo != "PLANTA" and o.equipo not in catalogo for o in ordenes.values()):
        raise ErrorCorpus("Equipo de orden histórica ausente del catálogo")

    secciones = {}
    for doc in fuentes.values():
        if isinstance(doc, DocumentoFuente):
            for seccion in doc.secciones:
                if (
                    seccion.id != f"{doc.id} §{seccion.numero}" or seccion.id in secciones
                    or seccion.pagina > doc.paginas
                ):
                    raise ErrorCorpus(f"Identidad o página de sección inválida: {seccion.id}")
                secciones[seccion.id] = seccion
    if fragmentos.keys() != {*secciones, *(f"historial {orden}" for orden in ordenes)}:
        raise ErrorCorpus("Faltan o sobran fragmentos respecto del índice oficial y las órdenes")

    chunks = tuple(
        _preparar_chunk(f, documentos, secciones, ordenes, fragmentos, catalogo)
        for f in sorted(fragmentos.values(), key=lambda f: f.id)
    )
    return CorpusPreparado(corpus.manifiesto, tuple(sorted(documentos.values(), key=lambda d: d.id)), chunks)


def _preparar_chunk(fragmento, documentos, secciones, ordenes, fragmentos, catalogo):
    documento = documentos.get(fragmento.doc)
    if documento is None:
        raise ErrorCorpus(f"Documento inexistente para {fragmento.id}")
    seccion, orden, codigos = None, None, ()
    equipos, vigente = documento.equipos, documento.vigente
    if fragmento.doc == "historial":
        orden = ordenes.get(fragmento.numero)
        if (
            orden is None or fragmento.id != f"historial {orden.id}"
            or fragmento.pagina is not None or fragmento.clave is not None
        ):
            raise ErrorCorpus(f"Orden histórica inconsistente: {fragmento.id}")
        equipos = (orden.equipo,) if orden.equipo in catalogo else ()
    else:
        seccion = secciones.get(fragmento.id)
        if (
            seccion is None or fragmento.id != f"{documento.id} §{fragmento.numero}"
            or (fragmento.titulo, fragmento.clave, fragmento.pagina, fragmento.vigente)
            != (seccion.titulo, seccion.clave, seccion.pagina, vigente)
        ):
            raise ErrorCorpus(f"Metadatos de sección inconsistentes: {fragmento.id}")
        if fragmento.id == "MBP-4 §6.1":
            codigos = correspondencias_fallas(fragmento)
            if {c.falla for c in codigos} != {f"F{i:02d}" for i in range(1, 11)}:
                raise ErrorCorpus("El resumen oficial de códigos de falla está incompleto")
    aplicabilidad = _aplicabilidad(fragmento, fragmentos)
    if aplicabilidad.estado == "no_aplicable":
        equipos = ()
    return Chunk(
        id=fragmento.id, documento=documento.id, titulo=fragmento.titulo, cita=fragmento.id,
        seccion=seccion, pagina=fragmento.pagina, vigente=vigente, equipos=equipos,
        alcance_equipos=("orden" if orden else "documento") if equipos else None,
        modelos=_modelos(equipos, catalogo), aplicabilidad=aplicabilidad, orden=orden,
        codigos_falla=codigos, texto=fragmento.texto,
        texto_embedding=f"{documento.titulo}\n{fragmento.id} · {fragmento.titulo}\n\n{fragmento.texto}",
        sha_texto=huella(fragmento.texto.encode()),
    )
