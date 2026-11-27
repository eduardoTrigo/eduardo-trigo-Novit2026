"""Compara un checkout del agente con capturas históricas, fuera del runtime."""

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import yaml
from planta_contrato import NotaRecorrida, RespuestaOperador
from planta_sim.dialogo import responder

from sistema.execution_budget import ConsumoEjecucion, ControlEjecucion, PresupuestoEjecucion
from sistema.part_agent import AgentePartes
from sistema.part_extraction import normalizar_texto
from sistema.part_store import ParteGuardado


ESCENARIOS = {"martes-tranquilo": "6322cd1a448d", "fuga-sin-enlace": "88b011179961"}


def capturas(archivo, escenario):
    oficial = json.loads((archivo / "resultados" / f"{escenario}.json").read_text(encoding="utf-8-sig"))
    sqlite = (archivo / f"sqlite-escenario-{ESCENARIOS[escenario]}.sqlite3").resolve()
    with sqlite3.connect(f"{sqlite.as_uri()}?mode=ro&immutable=1", uri=True) as db:
        registros = db.execute("""
            SELECT n.id, n.payload, n.transcripcion, p.contexto
            FROM notas n LEFT JOIN partes p ON n.id = p.nota_id
        """).fetchall()
    por_id = {identidad: (NotaRecorrida.model_validate_json(payload), stt, json.loads(contexto or "{}"))
              for identidad, payload, stt, contexto in registros}
    for nota in oficial["verdad"]:
        if "items" in nota and nota["nota_id"] in por_id:
            yield nota, *por_id[nota["nota_id"]]


def dialogar(nota, texto, verdad, catalogo, evidencia, metricas):
    rangos = {(e["fila"]["tag"], e["variable_sensor"]): (e["historico"]["minimo"], e["historico"]["maximo"])
              for e in evidencia if "minimo" in e["historico"]}

    def historial(tag, variable, ts):
        return [{"ts": (ts - timedelta(days=i)).isoformat(), "valor": v}
                for i, v in enumerate(rangos.get((tag, variable), ()), 1)]

    def control():
        return ControlEjecucion(nota.id, "evaluacion-local", PresupuestoEjecucion(), ConsumoEjecucion(),
                                inicio=time.monotonic(), reloj=time.monotonic, cancelada=lambda: False)

    agente = AgentePartes(lambda: catalogo, historial)
    plan = agente.nota(control(), nota, texto or "")
    primera = plan.estado
    filas, fotos = plan.filas or [], (plan.contexto or {}).get("fotos", [])
    motivos, vistos, confirmada, borrador = Counter(), set(), False, False
    for interaccion in range(3):
        if plan.mensaje is None:
            break
        if plan.mensaje.id in vistos:
            break  # Una respuesta virtual idéntica sería deduplicada en el almacén.
        vistos.add(plan.mensaje.id)
        metricas["mensajes"] += 1
        metricas["timestamp"] += plan.mensaje.ts == nota.ts
        if plan.estado == "borrador_pendiente":
            borrador = True
        else:
            tipo = plan.contexto["pendientes"][0]["tipo"]
            motivos[tipo] += 1
            justificada = tipo != "equipo" or any(a["tipo"] == "equipo" for a in verdad["ambiguedades"])
            metricas["preguntas_justificadas" if justificada else "preguntas_innecesarias"] += 1
        texto_operador, _ = responder(plan.mensaje.model_dump(mode="json"), [verdad], semilla=42)
        parte = ParteGuardado(nota, plan.estado, filas, plan.mensaje.id, interaccion + 1, plan.contexto)
        respuesta = RespuestaOperador(id=f"EVAL-{interaccion}", ts=nota.ts, mensaje_id=plan.mensaje.id,
                                      operador=nota.operador, texto=texto_operador, audio_url=None)
        siguiente = agente.respuesta(control(), parte, respuesta, None)
        if interaccion == 2 and siguiente.mensaje is not None:
            plan.estado = "revision_manual"
            break
        if siguiente.filas is not None:
            filas = siguiente.filas
        if siguiente.resultado == "confirmada":
            confirmada = True
        if siguiente.estado is None and interaccion < 2:
            continue
        plan = siguiente
    metricas["confirmadas"] += confirmada
    metricas["borradores"] += borrador
    metricas[f"primera_{primera}"] += 1
    return filas, fotos, primera, "confirmado" if confirmada else plan.estado, dict(motivos)


def campos(filas, fotos, items, metricas):
    esperadas = [item for item in items if item["variable"] != "condicion_insegura"]
    obtenidas = [f.model_dump() for f in filas]
    for campo in ("tag", "variable"):
        metricas[campo] += sum((Counter(x[campo] for x in esperadas) & Counter(x[campo] for x in obtenidas)).values())
    por_clave = {(f["tag"], f["variable"]): f for f in obtenidas}
    metricas["filas_esperadas"] += len(esperadas)
    for item in esperadas:
        clave = (item["tag"], item["variable"])
        fila = por_clave.get(clave, {})
        for campo in ("valor", "unidad"):
            if item[campo] is not None:
                metricas[f"{campo}_esperado"] += 1
                metricas[campo] += fila.get(campo) == item[campo]
        if item["observacion"]:
            metricas["observaciones_esperadas"] += 1
            texto = normalizar_texto(fila.get("observacion") or "").replace(",", "")
            esperado = normalizar_texto(item["observacion"]).replace(",", "")
            metricas["observaciones"] += esperado in texto
        if item["foto"]:
            metricas["fotos_esperadas"] += 1
            metricas["fotos"] += any((f["tag"], f["variable"], f["ruta"]) == (*clave, item["foto"]) for f in fotos)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captura", type=Path)
    parser.add_argument("catalogo", type=Path)
    args = parser.parse_args()
    catalogo = yaml.safe_load(args.catalogo.read_text(encoding="utf-8"))
    for fuente in ("stt", "referencia"):
        metricas = Counter()
        for escenario in ESCENARIOS:
            for verdad, nota, stt, contexto in capturas(args.captura, escenario):
                texto = stt if fuente == "stt" else verdad["texto"]
                filas, fotos, inicial, final, preguntas = dialogar(
                    nota, texto, verdad, catalogo, contexto.get("evidencia", []), metricas,
                )
                campos(filas, fotos, verdad["items"], metricas)
                print(json.dumps([fuente, escenario, nota.id, inicial, final, preguntas, len(filas)]))
        print(json.dumps({fuente: metricas}, sort_keys=True))


if __name__ == "__main__":
    main()
