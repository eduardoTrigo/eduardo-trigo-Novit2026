"""Exporta los recursos oficiales a un paquete local de ingesta."""

import argparse
import json
import re
import subprocess
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import yaml

from sistema.rag_corpus import (
    FUENTES,
    SHA_SIMULADOR,
    VERSION_FORMATO,
    EquipoFuente,
    OrdenFuente,
    huella,
    json_canonico,
    leer_corpus,
    preparar_corpus,
)


def exportar(repo: Path, commit: str, destino: Path) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Se requiere el SHA completo del commit oficial")
    repo = repo.resolve(strict=True)
    git = ["git", "-c", f"safe.directory={repo.as_posix()}", "-C", str(repo)]
    confirmado = subprocess.check_output([*git, "rev-parse", "--verify", f"{commit}^{{commit}}"])
    if confirmado.decode().strip() != commit:
        raise ValueError("La referencia debe identificar exactamente el commit oficial")
    fuentes = {
        nombre: subprocess.check_output([*git, "show", f"{commit}:{ruta}"])
        for nombre, ruta in FUENTES.items()
    }
    ordenes = json.loads(fuentes["ordenes.json"])
    equipos = yaml.safe_load(fuentes["equipos.json"])["equipos"]
    # El texto de las órdenes proviene de fragmentos.jsonl. Se proyectan solo metadatos documentales.
    contenido = fuentes | {
        "ordenes.json": json_canonico([
            {campo: orden[campo] for campo in OrdenFuente.model_fields if campo in orden}
            for orden in ordenes
        ]),
        "equipos.json": json_canonico([
            {campo: equipo[campo] for campo in EquipoFuente.model_fields if campo in equipo}
            for equipo in equipos
        ]),
    }
    contenido["manifiesto.json"] = json_canonico({
        "formato": VERSION_FORMATO,
        "sha_simulador": commit,
        "fuentes": {ruta: huella(fuentes[nombre]) for nombre, ruta in FUENTES.items()},
        "archivos": {nombre: huella(raw) for nombre, raw in contenido.items()},
    })
    memoria = BytesIO()
    with ZipFile(memoria, "w", compression=ZIP_DEFLATED) as archivo:
        for nombre, raw in contenido.items():
            # ZipInfo fija una fecha reproducible y permisos independientes del host.
            info = ZipInfo(nombre)
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archivo.writestr(info, raw, compress_type=ZIP_DEFLATED)
    paquete = memoria.getvalue()
    corpus = preparar_corpus(leer_corpus(paquete))
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("xb") as salida:
        salida.write(paquete)
    print(f"{corpus.identidad} {len(paquete)} bytes {destino}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulador", type=Path, required=True)
    parser.add_argument("--commit", default=SHA_SIMULADOR)
    parser.add_argument("--salida", type=Path, required=True)
    args = parser.parse_args()
    try:
        exportar(args.simulador, args.commit, args.salida)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"No se pudo exportar el corpus: {error}\n")


if __name__ == "__main__":
    main()
