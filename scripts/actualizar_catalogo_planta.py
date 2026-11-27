"""Genera el recurso empaquetado a partir de los blobs oficiales de un commit."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("simulador", type=Path)
    parser.add_argument("sha")
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.sha) is None:
        parser.error("Se requiere el SHA completo del commit oficial")
    ruta = args.simulador.resolve().as_posix()
    git = ["git", "-c", f"safe.directory={ruta}", "-C", ruta]
    archivos = {}

    def leer(nombre):
        datos = subprocess.check_output([*git, "show", f"{args.sha}:{nombre}"])
        archivos[nombre] = hashlib.sha256(datos).hexdigest()
        return datos.decode("utf-8")

    planta = yaml.safe_load(leer("datos/planta.yaml"))
    materiales = yaml.safe_load(leer("datos/materiales.yaml"))
    ordenes = json.loads(leer("datos/documentos/historial/ordenes.json"))
    equipos = {e["tag"]: {} for e in planta["equipos"]}
    for sensor in planta["sensores"]:
        equipos[sensor["tag"]][sensor["variable"]] = sensor["unidad"]
    datos = {
        "version": 1, "sha": args.sha, "archivos": archivos, "equipos": equipos,
        "equipos_archivo_ordenes": sorted({o["equipo"] for o in ordenes}), **materiales,
    }
    destino = Path(__file__).resolve().parents[1] / "src/sistema/recursos/catalogo_planta.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
