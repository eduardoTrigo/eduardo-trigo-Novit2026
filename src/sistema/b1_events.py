"""Construcción de eventos B1 y de su evidencia mínima."""

import json
import re
from datetime import datetime, timezone
from hashlib import sha256

from planta_contrato import EventoSistema, NotaRecorrida, TipoEvento


MAX_FOTOS_B1 = 3
_RUTA_FOTO = re.compile(r"/media/fotos/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.(?:jpg|jpeg|png)")


def ruta_foto_valida(ruta: str) -> bool:
    return len(ruta) <= 240 and _RUTA_FOTO.fullmatch(ruta) is not None


def referencias_fotos(nota: NotaRecorrida) -> list[dict]:
    referencias = []
    for ruta in dict.fromkeys(nota.fotos):
        valida = ruta_foto_valida(ruta)
        referencias.append({
            "ruta": ruta, "nota_id": nota.id, "asociacion": "nota",
            "estado": "pendiente" if valida else "invalida",
        })
    return referencias


def evento_de_parte(fila) -> tuple[EventoSistema, dict]:
    nota = NotaRecorrida.model_validate_json(fila["nota"])
    equipo, parte_id, version = (fila[campo] for campo in ("equipo", "parte_id", "version"))
    identidad = [nota.recorrida_id, nota.ts.astimezone(timezone.utc).isoformat(),
                 "parte", parte_id, version, equipo]
    id_evento = "B1-PARTE-" + sha256(json.dumps(identidad, ensure_ascii=False).encode()).hexdigest()
    tipo = TipoEvento(fila["resultado"])
    if tipo not in (TipoEvento.sin_novedad, TipoEvento.escalado):
        raise ValueError("Decisión de parte fuera de B1")
    reglas = json.loads(fila["evidencia"])
    if tipo == TipoEvento.escalado:
        causa = next(r for r in reglas if r["resultado"] == "Escalado")
        if causa["motivo"] in {"alarma_alta", "alarma_baja"}:
            texto = (f'{causa["variable_canonica"]}: {causa["valor_canonico"]} {causa["unidad_canonica"]} '
                     f'(límite {causa[causa["motivo"]]}).')
        else:
            texto = f'{(causa.get("observacion") or causa["motivo"])[:160]}.'
    else:
        texto = "Lecturas y observaciones evaluadas sin novedad."
    evento = EventoSistema(
        id=id_evento, ts=nota.ts, tipo=tipo, equipo=equipo, origen="parte",
        parte_id=parte_id, nota_ids=[nota.id], resumen=f"{equipo}. {texto} Evidencia {id_evento}.",
    )
    detalle = {
        "equipo": equipo, "origen": "parte", "ts": nota.ts.isoformat(), "parte_id": parte_id,
        "version": version, "nota_ids": [nota.id],
        "filas": [f for f in json.loads(fila["filas"]) if f["tag"] == equipo],
        "reglas": reglas, "huella_catalogo": fila["huella_catalogo"],
        "formato": 1, "fotos": referencias_fotos(nota), "tendencias": [],
    }
    if fila["huella_catalogo"] is None:
        detalle["motivo_huella_ausente"] = "decision_anterior_a_la_migracion"
    return evento, detalle


def evento_de_sensor(fila) -> tuple[EventoSistema, dict]:
    episodio = json.loads(fila["evidencia"])
    ultimo = episodio["evidencia"][-1]
    id_evento, equipo, variable = (fila[campo] for campo in ("id", "equipo", "variable"))
    direccion = fila["direccion"]
    ts = datetime.fromisoformat(fila["abierto"])
    duracion = int((ts - datetime.fromisoformat(fila["inicio"])).total_seconds())
    limite = ultimo.get(f"alarma_{direccion}")
    unidad, valor = ultimo["unidad_canonica"], ultimo["valor_canonico"]
    umbral = "" if limite is None else f", límite {limite}"
    evento = EventoSistema(
        id=id_evento, ts=ts, tipo=TipoEvento.escalado, equipo=equipo, origen="sensor",
        resumen=(f"{equipo}. {variable}: alarma {direccion} sostenida durante {duracion} s simulados "
                 f"({valor} {unidad}{umbral}). Evidencia {id_evento}."),
    )
    return evento, {
        "origen": "sensor", "equipo": equipo, "variable": variable, "ts": ts.isoformat(),
        "episodio": episodio, "huella_limites": fila["huella_limites"], "epoca": fila["epoca"],
        "formato": 1, "parte_id": None, "nota_ids": [], "fotos": [], "tendencias": [],
    }
