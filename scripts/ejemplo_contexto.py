"""Prueba de solo lectura de catálogo, RAG y memoria con el stack existente."""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone

import psycopg
from pydantic import ValidationError

from sistema.context_builder import ErrorContexto, construir_contexto, proyectar_memoria
from sistema.context_builder_models import (
    Alcance, DatosContexto, EntradaContexto, FalloFuente, Lectura, LecturaMemoria, NotaContexto,
)
from sistema.part_memory import RepositorioMemoriaPostgres
from sistema.part_memory_store import AlmacenMemoriaLocal
from sistema.plant_tool_models import Equipo, ErrorTool, Pagina
from sistema.plant_tools import CatalogoToolsPlanta
from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres
from sistema.rag_retrieval import ResultadoManuales


def construir_ejemplo(nota_id: str, equipo: str, consulta: str):
    # Una nota ya existente, dos consultas de catálogo y ninguna escritura.
    config = ConfiguracionPostgres.desde_entorno()
    remoto = RepositorioMemoriaPostgres(config)
    local = AlmacenMemoriaLocal(os.environ["PIA3_DB_PATH"])
    captura = remoto.obtener(nota_id)
    if captura is None:
        raise ErrorContexto("nota_de_ejemplo_ausente")
    memoria = proyectar_memoria(captura)
    revision = local.revision(nota_id)
    if revision != memoria.revision:
        raise ErrorContexto("nota_de_ejemplo_no_sincronizada")
    catalogo = CatalogoToolsPlanta(os.getenv("PLANTA_API_URL", "http://sim:8100"))
    fallos = []

    def consultar(tool, modelo, argumentos):
        try:
            filas = catalogo.ejecutar(tool, argumentos)
            return Lectura(obtenida_en=datetime.now(timezone.utc), dato=modelo.model_validate_json(json.dumps(filas)))
        except ErrorTool as error:
            estado = {
                "tiempo_agotado": "tiempo_agotado", "fuente_no_disponible": "no_disponible",
            }.get(error.codigo, "invalida")
            fallos.append(FalloFuente(fuente=tool, estado=estado))
            return None
        except ValidationError:
            fallos.append(FalloFuente(fuente=tool, estado="invalida"))
            return None

    equipos = consultar("consultar_equipos", Pagina[Equipo], {"tag": equipo, "limite": 1})
    documentos = consultar("buscar_manuales", ResultadoManuales, {"consulta": consulta, "top_k": 2})
    solicitud = EntradaContexto(
        nota=NotaContexto(id=memoria.nota_id, ts=memoria.ts_nota, texto=consulta),
        tarea="documentacion", evaluado_en=max(datetime.now(timezone.utc), memoria.ts_nota, memoria.actualizada_en),
        alcance=Alcance(equipos=(equipo,)),
        datos=DatosContexto(equipos=equipos, documentos=documentos, fallos=tuple(fallos)),
        memoria=LecturaMemoria(revision_sqlite=revision, postgres=memoria),
    )
    return construir_contexto(solicitud)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nota-id", required=True, help="ID de una nota ya sincronizada y autorizada")
    parser.add_argument("--equipo", required=True)
    parser.add_argument("--consulta", required=True)
    args = parser.parse_args()
    try:
        resultado = construir_ejemplo(args.nota_id, args.equipo, args.consulta)
    except ErrorContexto as error:
        parser.exit(1, f"Ejemplo interrumpido ({error.codigo}).\n")
    except (ErrorConexionPostgres, psycopg.Error, sqlite3.Error, KeyError, ValueError) as error:
        parser.exit(1, f"No se pudo construir el ejemplo con las fuentes existentes ({type(error).__name__}).\n")
    contexto = resultado.contexto
    if not (contexto.documentos and contexto.resultados_tools and contexto.memoria.datos):
        parser.exit(1, "El ejemplo requiere un resultado de cada una de las tres fuentes.\n")
    # Publica evidencia resumida y omite la transcripción, las respuestas y los datos de operador.
    print(json.dumps({
        "tarea": contexto.entrada_actual.tarea,
        "corpus": contexto.documentos[0].corpus,
        "modo": contexto.documentos[0].modo,
        "citas": [d.resultado.cita for d in contexto.documentos],
        "equipos": [b.datos["tag"] for b in contexto.resultados_tools if b.fuente == "consultar_equipos"],
        "memoria": {
            "origen": contexto.memoria.origen, "sincronizacion": contexto.memoria.sincronizacion,
            "versiones": len(contexto.memoria.datos.versiones), "respuestas": len(contexto.memoria.datos.respuestas),
        },
        "uso": resultado.uso.model_dump(mode="json"),
    }, ensure_ascii=True))


if __name__ == "__main__":
    main()
