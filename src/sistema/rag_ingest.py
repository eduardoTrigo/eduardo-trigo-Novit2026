"""Ingesta documental explícita, estadísticas y verificación del corpus PIA-3."""

import argparse
import json
import re
import sys
from pathlib import Path
from time import monotonic

import psycopg

from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres
from sistema.rag_corpus import MAX_ARCHIVO, ErrorCorpus, leer_corpus, preparar_corpus
from sistema.rag_store import consultar_estado, ingestar


def _id_corpus(texto: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", texto):
        raise argparse.ArgumentTypeError("Se requiere una identidad de corpus SHA-256")
    return texto


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)
    carga = comandos.add_parser("ingestar", help="Importar una versión documental")
    carga.add_argument("--corpus", required=True, help="Paquete ZIP o - para leer stdin")
    verificacion = comandos.add_parser("verificar", help="Recalcular huellas y comprobar integridad")
    verificacion.add_argument("--id", type=_id_corpus, required=True)
    estadisticas = comandos.add_parser("stats", help="Consultar estadísticas por corpus y espacio")
    estadisticas.add_argument("--id", type=_id_corpus)
    args = parser.parse_args()
    inicio = monotonic()
    try:
        config = ConfiguracionPostgres.desde_entorno()
        if args.comando == "ingestar":
            if args.corpus == "-":
                raw = sys.stdin.buffer.read(MAX_ARCHIVO + 1)
            else:
                with Path(args.corpus).open("rb") as archivo:
                    raw = archivo.read(MAX_ARCHIVO + 1)
            corpus = preparar_corpus(leer_corpus(raw))
            cambios = ingestar(config, corpus)
            resultado = {"corpus": corpus.identidad, "cambios": cambios, "chunks": len(corpus.chunks)}
        else:
            resultado = {"corpus": consultar_estado(config, args.id, verificar=args.comando == "verificar")}
    except (ErrorConexionPostgres, ErrorCorpus, ValueError) as error:
        parser.exit(1, f"{error}\n")
    except OSError:
        parser.exit(1, "No se pudo leer el paquete documental\n")
    except psycopg.Error as error:
        parser.exit(1, f"Falló una operación RAG en PostgreSQL ({error.sqlstate or 'sin SQLSTATE'})\n")
    resultado["segundos"] = round(monotonic() - inicio, 3)
    print(json.dumps(resultado, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
