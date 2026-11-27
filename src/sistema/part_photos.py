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
_CUES = {
    "goteo_empaquetadura": r"\b(?:empaquetadura|goteo)\b",
    "presion_descarga_local": r"\b(?:manometro de descarga|presion de descarga)\b",
    "presion_succion": r"\b(?:manometro de succion|presion de succion)\b",
    "presion_diferencial": r"\b(?:manometro diferencial|diferencial)\b",
    "temperatura_rodamiento_ir": r"\brodamiento\b",
    "temperatura_motor_ir": r"\bmotor\b",
    "nivel_aceite_visor": r"\bvisor\b",
    "estado_correas": r"\bcorreas\b",
    "nivel": r"\bnivel\b",
}
_Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


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
    if len(menciones) != 1:
        return {}
    frase, prefijo, objetivo = menciones[0]
    if re.search(r"\b(?:foto|fotos|no|o|quizas|creo)\b", prefijo + " " + objetivo):
        return {}
    aliases = indexar_aliases(catalogo)
    if not aliases:
        return {}
    patrones = [re.escape(a) for a in sorted(aliases, key=len, reverse=True)]
    patron_aliases = "(" + "|".join(patrones) + r")(?![\w-])"
    referencias = re.findall(r"(?<![\w-])" + patron_aliases, objetivo)
    objetivos = re.findall(r"(?:^\s*|\b(?:de|del)\s+)" + patron_aliases, objetivo)
    tags = {tag for alias in referencias for tag in aliases[alias]}
    if not objetivos or len(tags) != 1:
        return {}
    candidatas = [f for f in filas if f.tag in tags and re.search(_CUES.get(f.variable, r"(?!)"), objetivo)]
    if len(candidatas) != 1:
        return {}
    fila = candidatas[0]
    return {"tag": fila.tag, "variable": fila.variable, "estado_asociacion": "fila",
            "procedencia": "mencion_explicita", "evidencia": frase, "motivo": None}


def capturar_fotos(nota: NotaRecorrida, texto, catalogo, filas, descargar, control, aislador):
    rutas = list(dict.fromkeys(nota.fotos))
    asociacion = _asociacion(texto, catalogo, filas) if len(rutas) == 1 and ruta_foto_valida(rutas[0]) else {}
    if len(rutas) > 1:
        asociacion = {"motivo": "varias_referencias_sin_correspondencia"}
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
