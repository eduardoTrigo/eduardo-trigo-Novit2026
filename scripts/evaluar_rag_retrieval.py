"""Evaluación local y de solo lectura con el evaluador oficial de Novit."""

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from time import perf_counter

import psycopg

from sistema.plant_tool_models import ErrorTool
from sistema.postgres import ConfiguracionPostgres, ErrorConexionPostgres
from sistema.rag_corpus import SHA_SIMULADOR
from sistema.rag_retrieval import ConsultaManuales, RecuperadorPostgres
from sistema.rag_store import consultar_estado


K = (1, 3, 5, 10)
SHA_EVALUADOR = "c9f3a3d2996fea8f95a1cd337a1cf7afbecafad2d443c7bcdc6fa533646ac19b"
SHA_PREGUNTAS = "f464ea868cabe9971882975530a928e6590a36166c4be1bc2e6568cf4045a9d5"
SHA_FRAGMENTOS = "a07975aaafd560c7017384c3590237b0cacb5a62ce002847e7bfafd576fd8a81"


def evaluar_variante(evaluar: Callable, buscar: Callable, suite: list[dict]) -> dict:
    citas_por_pregunta = []
    latencias = []

    def medir(pregunta: str, k: int) -> list[str]:
        inicio = perf_counter()
        citas = list(dict.fromkeys(buscar(pregunta, k)))[:k]
        latencias.append((perf_counter() - inicio) * 1000)
        citas_por_pregunta.append(citas)
        return citas

    informe = evaluar(medir, suite, K)
    cuenta_revisiones = 0
    solo_c = 0
    cuenta_citas_evaluadas = 0
    solo_c_evaluada = 0
    for pregunta, fila, citas in zip(suite, informe["preguntas"], citas_por_pregunta, strict=True):
        rev_c = next((i for i, cita in enumerate(citas, 1) if cita.startswith("TX350-C §")), None)
        rev_d = next((i for i, cita in enumerate(citas, 1) if cita.startswith("TX350-D §")), None)
        precede = rev_c is not None and rev_d is not None and rev_c < rev_d
        cuenta_revisiones += precede
        solo_c += rev_c is not None and rev_d is None
        c_evaluadas = {cita for cita in pregunta["distractores"] if cita.startswith("TX350-C §")}
        d_evaluadas = {cita for cita in pregunta["relevantes"] if cita.startswith("TX350-D §")}
        c_evaluada = next((i for i, cita in enumerate(citas, 1) if cita in c_evaluadas), None)
        d_evaluada = next((i for i, cita in enumerate(citas, 1) if cita in d_evaluadas), None)
        precede_evaluadas = c_evaluada is not None and d_evaluada is not None and c_evaluada < d_evaluada
        cuenta_citas_evaluadas += precede_evaluadas
        solo_c_evaluada += bool(d_evaluadas) and c_evaluada is not None and d_evaluada is None
        fila.update(citas=citas, primera_C=rev_c, primera_D=rev_d, C_antes_D=precede,
                    primera_C_distractora=c_evaluada, primera_D_relevante=d_evaluada)

    for tipo, grupo in informe["por_tipo"].items():
        filas = [fila for fila in informe["preguntas"] if fila["tipo"] == tipo]
        for metrica in ("mrr", *(f"distractor@{k}" for k in K)):
            grupo[metrica] = round(statistics.mean(f[metrica] for f in filas), 3)
    informe.update(
        rev_C_antes_D=cuenta_revisiones,
        rev_C_sin_D=solo_c,
        rev_C_distractora_antes_D_relevante=cuenta_citas_evaluadas,
        rev_C_distractora_sin_D_relevante=solo_c_evaluada,
        sin_acierto_10=[f["id"] for f in informe["preguntas"] if not f["hit@10"]],
        sin_acierto_20=[f["id"] for f in informe["preguntas"] if not f["mrr"]],
        latencias_ms={
            "p50": round(statistics.median(latencias), 3),
            "p95": round(sorted(latencias)[math.ceil(0.95 * len(latencias)) - 1], 3),
        },
    )
    return informe


def _adaptar(buscar: Callable) -> Callable[[str, int], list[str]]:
    def citas(pregunta: str, k: int) -> list[str]:
        resultados = buscar(ConsultaManuales(consulta=pregunta, top_k=k)).resultados
        return [fila.cita for fila in resultados]

    return citas


def _fuentes(simulador: Path):
    planta = simulador.resolve(strict=True) / "planta"
    documentos = planta.parent / "datos/documentos"
    for ruta, esperado in (
        (planta / "corpus/evaluar.py", SHA_EVALUADOR),
        (documentos / "evaluacion/retrieval.json", SHA_PREGUNTAS),
        (documentos / "fragmentos.jsonl", SHA_FRAGMENTOS),
    ):
        # Normaliza los finales de línea de un checkout Git en Windows.
        if hashlib.sha256(ruta.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != esperado:
            raise ValueError(f"La fuente oficial no coincide con el SHA esperado: {ruta.name}")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(planta))
    oficial = import_module("corpus.evaluar")
    suite, fragmentos = oficial.cargar(documentos)
    return oficial, suite, fragmentos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulador", type=Path, required=True)
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--salida", type=Path, help="Ruta nueva para el informe JSON local")
    args = parser.parse_args()
    try:
        oficial, suite, fragmentos = _fuentes(args.simulador)
        variantes = {"bm25_oficial": evaluar_variante(oficial.evaluar, oficial.BM25(fragmentos).buscar, suite)}
        config = ConfiguracionPostgres.desde_entorno()
        buscador = RecuperadorPostgres(config, args.corpus_id)
        (estado,) = consultar_estado(config, buscador.corpus, verificar=True)
        fuentes = estado["manifiesto"]["fuentes"]
        if (
            estado["manifiesto"]["sha_simulador"] != SHA_SIMULADOR
            or fuentes.get("datos/documentos/fragmentos.jsonl") != SHA_FRAGMENTOS
            or estado["chunks"] != len(fragmentos)
        ):
            raise ValueError("El corpus de PostgreSQL difiere de la línea de base oficial")
        variantes["fts_base"] = evaluar_variante(oficial.evaluar, _adaptar(buscador.buscar_base), suite)
        variantes["fts_operativa"] = evaluar_variante(oficial.evaluar, _adaptar(buscador.buscar), suite)
        informe = {
            "corpus": buscador.corpus, "sha_simulador": SHA_SIMULADOR,
            "sha_evaluador": SHA_EVALUADOR, "sha_suite": SHA_PREGUNTAS, "variantes": variantes,
        }
        if args.salida is not None:
            with args.salida.open("x", encoding="utf-8") as archivo:
                json.dump(informe, archivo, ensure_ascii=False, indent=2)
                archivo.write("\n")
        print(json.dumps({nombre: datos["total"] for nombre, datos in variantes.items()}, indent=2))
    except (OSError, ValueError, ErrorTool, ErrorConexionPostgres) as error:
        parser.exit(1, f"Evaluación RAG interrumpida: {error}\n")
    except psycopg.Error:
        parser.exit(1, "Evaluación RAG interrumpida al verificar el corpus PostgreSQL\n")


if __name__ == "__main__":
    main()
