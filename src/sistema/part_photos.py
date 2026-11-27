"""Metadatos y captura acotada de fotos en el worker de partes."""

import json
import re
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal

import httpx
from planta_contrato import FilaParte, NotaRecorrida
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sistema.b1_events import MAX_FOTOS_B1, ruta_foto_valida
from sistema.execution_budget import CorteEjecucion
from sistema.part_context import MAX_BYTES_FOTO_B1
from sistema.part_extraction import indexar_aliases, normalizar_texto


PLAZO_FOTO_S = 0.75
MAX_BYTES_FOTOS_PARTE = 64 * 1024 * 1024
_MENCION = re.compile(r"\bfoto\s+d(?:e|el)\b")
_LUGAR = re.compile(
    r"\b(?:(?:cerca|al\s+lado|detras|delante|debajo|encima|alrededor|enfrente|"
    r"en\s+las\s+inmediaciones)\s+del?|(?:junto|frente|proxim[oa])\s+al?)\s+[^,]*"
)
_COORDINACION = re.compile(r"\b(?:y|e|o|u|ni)\b")
_CUES = {
    "goteo_empaquetadura": r"\b(?:empaquetadura|goteo)\b",
    "presion_descarga_local": r"\b(?:manometro de descarga|presion de descarga)\b",
    "presion_succion": r"\b(?:manometro de succion|presion de succion)\b",
    "presion_diferencial": r"\b(?:manometro diferencial|diferencial)\b",
    "temperatura_rodamiento_ir": r"\brodamiento\b",
    "temperatura_motor_ir": r"\bmotor\b",
    "nivel_aceite_visor": r"\bvisor\b",
    "estado_correas": r"\bcorreas\b",
    "nivel": r"\bnivel\b(?!\s+(?:de|del|en)\s+(?:el\s+)?(?:aceite|visor)\b)",
}
_Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ObjetivoFoto(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    tag: str
    variable: str


class FotoParte(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: _Hash
    nota_id: str
    ts: datetime
    ruta: str
    tag: str | None = None
    variable: str | None = None
    estado_asociacion: Literal["nota", "fila"] = "nota"
    procedencia: Literal["nota", "mencion_explicita"] = "nota"
    evidencia: str | None = None
    motivo: str | None = "sin_mencion_inequivoca"
    objetivo_pendiente: ObjetivoFoto | None = None
    estado_descarga: Literal[
        "sin_fuente", "invalida", "ausente", "timeout", "no_disponible", "omitida_limite",
        "omitida_presupuesto", "descargada",
    ] = "sin_fuente"
    sha256: _Hash | None = None
    mime: Literal["image/jpeg", "image/png"] | None = None
    bytes: Annotated[int, Field(gt=0, le=MAX_BYTES_FOTO_B1)] | None = None

    @model_validator(mode="after")
    def coherencia(self):
        if self.estado_asociacion == "fila":
            if not (self.tag and self.variable and self.evidencia
                    and self.procedencia == "mencion_explicita" and self.motivo is None):
                raise ValueError("Asociación a fila sin evidencia")
        elif self.tag is not None or self.variable is not None or self.procedencia != "nota" or not self.motivo:
            raise ValueError("Asociación a nota incoherente")
        pendiente = self.motivo == "fila_pendiente"
        if pendiente != (self.objetivo_pendiente is not None) or (pendiente and not self.evidencia):
            raise ValueError("Asociación pendiente sin objetivo explícito")
        binario = (self.sha256, self.mime, self.bytes)
        if (self.estado_descarga == "descargada" and not all(binario)
                or self.estado_descarga != "descargada" and any(binario)):
            raise ValueError("Metadatos binarios incoherentes")
        return self


def _asociacion(texto, catalogo, filas):
    menciones = []
    for frase in re.split(r"[.!?;\n]+", texto):
        normal = normalizar_texto(frase)
        if mencion := _MENCION.search(normal):
            menciones.append((frase.strip(), normal[:mencion.start()], normal[mencion.end():]))
    if not menciones:
        return {}
    if len(menciones) > 1:
        return {"motivo": "mencion_ambigua"}
    frase, prefijo, objetivo = menciones[0]
    if re.search(r"\b(?:foto|fotos|no|quizas|creo)\b", prefijo + " " + objetivo):
        return {"motivo": "mencion_ambigua"}
    aliases = indexar_aliases(catalogo)
    if not aliases:
        return {}
    patrones = [re.escape(a) for a in sorted(aliases, key=len, reverse=True)]
    patron_aliases = "(" + "|".join(patrones) + r")(?![\w-])"
    referencia = re.compile(r"(?<![\w-])" + patron_aliases)
    # Un complemento de lugar puede incluir equipos y nombres de variables. Si su
    # coordinación no enumera solo referencias de lugar, el alcance es incierto.
    otro_lugar = re.compile(r"\s*(?:(?:de|del|a|al)\s+)?" + patron_aliases + r"\s*")
    for lugar in _LUGAR.finditer(objetivo):
        principal, *coordinados = _COORDINACION.split(lugar[0])
        if len(referencia.findall(principal)) > 1 or any(not otro_lugar.fullmatch(tramo) for tramo in coordinados):
            return {"motivo": "mencion_ambigua"}
    objetivo = _LUGAR.sub(" ", objetivo)
    if re.search(r"\bo\b", prefijo + " " + objetivo):
        return {"motivo": "mencion_ambigua"}
    referencias = referencia.findall(objetivo)
    objetivos = re.findall(r"(?:^\s*|\b(?:de|del)\s+)" + patron_aliases, objetivo)
    tags = {tag for alias in referencias for tag in aliases[alias]}
    variables = {variable for variable, patron in _CUES.items() if re.search(patron, objetivo)}
    if len(tags) > 1 or len(variables) > 1:
        return {"motivo": "mencion_ambigua"}
    if not objetivos or not tags or not variables:
        return {}
    metadata = {"objetivo_pendiente": {"tag": tags.pop(), "variable": variables.pop()},
                "evidencia": frase, "motivo": "fila_pendiente"}
    return _vincular(metadata, {(f.tag, f.variable) for f in filas})


def _vincular(foto, claves):
    objetivo = foto.get("objetivo_pendiente")
    if foto.get("motivo") == "fila_pendiente" and objetivo and (objetivo["tag"], objetivo["variable"]) in claves:
        return foto | objetivo | {"estado_asociacion": "fila", "procedencia": "mencion_explicita",
                                  "motivo": None, "objetivo_pendiente": None}
    return foto


def hay_presupuesto_asociacion(control):
    return control.plazo_lectura() > 0 and control.consumo.pasos < control.presupuesto.pasos - 1


def reconsiderar_fotos(fotos: list[dict], filas: list[FilaParte]) -> list[dict]:
    claves = {(f.tag, f.variable) for f in filas}
    return [_vincular(foto, claves) for foto in fotos]


def capturar_fotos(nota: NotaRecorrida, texto, catalogo, filas, descargar, control, aislador):
    rutas = list(dict.fromkeys(nota.fotos))
    asociacion = {}
    if len(rutas) > 1:
        asociacion = {"motivo": "varias_referencias_sin_correspondencia"}
    elif rutas and texto and catalogo and ruta_foto_valida(rutas[0]):
        if hay_presupuesto_asociacion(control):
            asociacion = control.paso("analizar_asociacion_foto", lambda: _asociacion(texto, catalogo, filas))
        else:
            asociacion = {"motivo": "asociacion_omitida_presupuesto"}
    fotos, archivos = [], {}
    validas = 0
    for ruta in rutas:
        metadatos = dict(asociacion)
        if ruta_foto_valida(ruta):
            validas += 1
            if validas > MAX_FOTOS_B1:
                metadatos["estado_descarga"] = "omitida_limite"
            elif descargar is not None:
                datos = _capturar(ruta, descargar, control, aislador)
                if isinstance(datos, str):
                    metadatos["estado_descarga"] = datos
                else:
                    mime, contenido = datos
                    huella = sha256(contenido).hexdigest()
                    archivos[huella] = (mime, contenido)
                    metadatos.update(estado_descarga="descargada", sha256=huella, mime=mime, bytes=len(contenido))
        else:
            metadatos["estado_descarga"] = "invalida"
        identidad = sha256(json.dumps([nota.id, ruta], ensure_ascii=False).encode()).hexdigest()
        foto = FotoParte(id=identidad, nota_id=nota.id, ts=nota.ts, ruta=ruta, **metadatos)
        fotos.append(foto.model_dump(mode="json"))
    return fotos, archivos


def _capturar(ruta, descargar, control, aislador):
    if (control.plazo_lectura() <= PLAZO_FOTO_S + 0.05
            or control.consumo.pasos >= control.presupuesto.pasos - 1):
        return "omitida_presupuesto"
    try:
        return control.paso("descargar_foto", lambda: descargar(ruta, plazo=PLAZO_FOTO_S), aislador=aislador,
                            plazo_local=PLAZO_FOTO_S)
    except (TimeoutError, httpx.TimeoutException):
        return "timeout"
    except httpx.HTTPStatusError as error:
        return "ausente" if error.response.status_code in {404, 410} else "no_disponible"
    except httpx.RemoteProtocolError:
        return "invalida"
    except httpx.HTTPError:
        return "no_disponible"
    except ValueError:
        return "invalida"
    except CorteEjecucion as error:
        if error.motivo == "fuente_no_disponible":
            return "no_disponible"
        raise


def revalidar_fotos(contexto: dict, filas: list[FilaParte]) -> None:
    claves = {(f.tag, f.variable) for f in filas}
    for foto in contexto.get("fotos", []):
        if foto["estado_asociacion"] == "fila" and (foto["tag"], foto["variable"]) not in claves:
            foto.update(tag=None, variable=None, estado_asociacion="nota", procedencia="nota",
                        motivo="cambio_identidad_fila")
