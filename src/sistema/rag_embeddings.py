"""Contrato acotado de embeddings para el índice documental."""

import math
import struct
from collections.abc import Sequence
from time import monotonic
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue

from sistema.rag_corpus import Contrato, NoVacio, huella, json_canonico


class ErrorEmbedding(RuntimeError):
    """El proveedor o sus vectores no cumplen el contrato."""


class ModeloEmbedding(Contrato):
    nombre: NoVacio
    revision: NoVacio
    dimensiones: Annotated[int, Field(ge=1, le=16000)]
    ambito: Literal["operativo", "prueba"]
    configuracion: dict[str, JsonValue]

    @property
    def identidad(self) -> str:
        try:
            return huella(json_canonico(self.model_dump(mode="json")))
        except ValueError:
            raise ErrorEmbedding("La configuración del modelo contiene valores no finitos") from None


class ProveedorEmbedding(Protocol):
    @property
    def modelo(self) -> ModeloEmbedding: ...

    @property
    def max_tokens(self) -> int: ...

    def contar_tokens(self, texto: str) -> int: ...

    def documentos(self, textos: Sequence[str], *, timeout_s: float) -> Sequence[Sequence[float]]: ...

    def consultas(self, textos: Sequence[str], *, timeout_s: float) -> Sequence[Sequence[float]]: ...


def generar_vectores(
    proveedor: ProveedorEmbedding, esperado: ModeloEmbedding, textos: Sequence[str],
    *, para_consultas: bool = False,
) -> list[list[float]]:
    identidad = esperado.identidad
    if proveedor.modelo.identidad != identidad:
        raise ErrorEmbedding("Proveedor incompatible con el espacio vectorial solicitado")
    limite = proveedor.max_tokens
    if type(limite) is not int or limite < 1:
        raise ErrorEmbedding("El proveedor no declara un límite de tokens válido")

    vectores = []
    vence = monotonic() + 120
    generar = proveedor.consultas if para_consultas else proveedor.documentos
    for inicio in range(0, len(textos), 32):
        lote = textos[inicio:inicio + 32]
        try:
            conteos = [proveedor.contar_tokens(texto) for texto in lote]
            if any(type(n) is not int or not 1 <= n <= limite for n in conteos):
                raise ErrorEmbedding("Un fragmento excede el límite de tokens o tiene un conteo inválido")
            restante = vence - monotonic()
            if restante <= 0:
                raise ErrorEmbedding("Se agotó el tiempo de indexación")
            timeout = min(15, restante)
            inicio_lote = monotonic()
            respuesta = generar(lote, timeout_s=timeout)
        except ErrorEmbedding:
            raise
        except Exception:
            raise ErrorEmbedding("Falló el proveedor de embeddings") from None
        if monotonic() - inicio_lote > timeout:
            raise ErrorEmbedding("Se agotó el tiempo del lote de embeddings")
        if proveedor.modelo.identidad != identidad:
            raise ErrorEmbedding("El proveedor cambió de espacio vectorial durante el procesamiento")
        if not isinstance(respuesta, Sequence) or len(respuesta) != len(lote):
            raise ErrorEmbedding("El proveedor devolvió una cantidad de vectores incorrecta")
        vectores.extend(_vector_float32(vector, esperado.dimensiones) for vector in respuesta)
    return vectores


def _vector_float32(vector, dimensiones: int) -> list[float]:
    if not isinstance(vector, Sequence) or len(vector) != dimensiones:
        raise ErrorEmbedding("Dimensión de vector incorrecta")
    resultado = []
    for valor in vector:
        try:
            if type(valor) not in (float, int) or not math.isfinite(valor):
                raise ValueError
            resultado.append(struct.unpack("!f", struct.pack("!f", valor))[0])
        except (ValueError, OverflowError, struct.error):
            raise ErrorEmbedding("El vector contiene un valor inválido para pgvector") from None
    if not any(resultado):
        raise ErrorEmbedding("El vector nulo no admite distancia coseno")
    return resultado
