"""Extracción local de lecturas claras a partir de una transcripción."""

import math
import re
import unicodedata
from dataclasses import dataclass, field

from planta_contrato import FilaParte


_CORTOS = {
    palabra: valor for valor, palabra in enumerate(
        "cero uno dos tres cuatro cinco seis siete ocho nueve diez "
        "once doce trece catorce quince dieciseis diecisiete dieciocho "
        "diecinueve veinte veintiuno veintidos veintitres veinticuatro "
        "veinticinco veintiseis veintisiete veintiocho veintinueve".split()
    )
}
_CORTOS.update({"un": 1, "una": 1, "veintiun": 21, "veintiuna": 21})
_DECENAS = dict(zip(
    "treinta cuarenta cincuenta sesenta setenta ochenta noventa".split(),
    range(30, 100, 10),
))
_CENTENAS = {"cien": 100, "ciento": 100}
for _valor, _palabra in enumerate(
    "doscientos trescientos cuatrocientos quinientos seiscientos "
    "setecientos ochocientos novecientos".split(), start=2,
):
    _CENTENAS[_palabra] = _CENTENAS[_palabra[:-2] + "as"] = _valor * 100

_PALABRAS = "|".join(sorted(
    _CORTOS.keys() | _DECENAS.keys() | _CENTENAS.keys() | {"mil"}, key=len, reverse=True
))
_NUMERAL = rf"(?:[+-]?\d+(?:[.,]\d+)?|{_PALABRAS})\b"
_NUMEROS = re.compile(
    rf"(?<![\w-]){_NUMERAL}(?:\s+(?:(?:y|con|coma|punto)\s+)?{_NUMERAL})*"
    rf"(?:\s+y\s+medio\b)?"
)
_FRASES = re.compile(r"[!?;\n]+|(?<!\d)\.|\.(?!\d)")
_CLAUSULAS = re.compile(
    r"(?<!\d),|,(?!\d)|\by\s+(?=(?:(?:el|la|las)\s+)?"
    r"(?:rodamiento|motor|temperatura|presion|descarga|diferencial|nivel|correas|"
    r"gotea|pierde|sigue goteando|visor|hace|tiene)\b)"
)
_INCERTIDUMBRE = re.compile(
    r"\b(?:no|mas o menos|aproximadamente|alrededor de|quizas|creo|entre)\b"
)
_UNIDADES = {
    "kilo": "kg/cm2", "kilos": "kg/cm2", "kilogramos": "kg/cm2", "kg/cm2": "kg/cm2",
    "psi": "psi", "libras": "psi", "libra": "psi", "lb": "psi", "bar": "bar", "bares": "bar",
    "degc": "degC", "°c": "degC", "grados": "degC", "grados celsius": "degC",
    "m": "m", "metro": "m", "metros": "m", "m3/d": "m3/d",
    "gotas por minuto": "gotas/min", "gotas/min": "gotas/min",
}
_UNIDAD = re.compile(r"(?<!\w)(?:" + "|".join(
    re.escape(u) for u in sorted(_UNIDADES, key=len, reverse=True)
) + r")(?!\w)")
_PRESIONES = {"kg/cm2", "psi", "bar"}
_CUANTITATIVAS = (
    ("presion_descarga_local", "bomba", r"\bdescarga\b", _PRESIONES),
    ("presion_succion", "bomba", r"\bsuccion\b", _PRESIONES),
    ("presion_diferencial", "filtro", r"\bdiferencial\b", _PRESIONES),
    ("temperatura_rodamiento_ir", "bomba", r"\brodamiento\b", {"degC"}),
    ("temperatura_motor_ir", "bomba", r"\b(?:temperatura del motor|motor)\b", {"degC"}),
    ("nivel", "tanque", r"\b(?:nivel|m|metros?)\b", {"m"}),
    ("caudal", "caudalimetro", r"\bcaudal\b", {"m3/d"}),
)
_CUALITATIVAS = (
    ("estado_correas", r"\bcorreas\b"),
    ("nivel_aceite_visor", r"\b(?:visor de aceite|aceite en el visor)\b"),
    ("estado", r"\b(?:parada|disparada|no arranca)\b"),
    ("goteo_empaquetadura", r"\b(?:gotea|goteando|goteo|pierde|chorrea)\b.*\bempaquetadura\b"),
    ("ruido", r"\b(?:golpeteo|ruido|suena)\b"),
)


@dataclass(frozen=True)
class ResultadoExtraccion:
    filas: list[FilaParte]
    pendiente: bool
    dudas: list[dict] = field(default_factory=list)
    no_interpretada: bool = False


def normalizar_texto(texto: str) -> str:
    return "".join(
        letra for letra in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(letra)
    )


def _entero(tokens: list[str]) -> int | None:
    if not tokens:
        return None
    if len(tokens) == 1 and tokens[0].lstrip("+-").isdigit():
        return int(tokens[0])
    if "mil" in tokens:
        antes = tokens.index("mil")
        miles = _entero(tokens[:antes]) if antes else 1
        resto = _entero(tokens[antes + 1:]) if tokens[antes + 1:] else 0
        if miles is not None and resto is not None and 0 < miles < 1000 and 0 <= resto < 1000:
            return miles * 1000 + resto
        return None
    if tokens[0] in _CENTENAS:
        resto = _entero(tokens[1:]) if len(tokens) > 1 else 0
        return _CENTENAS[tokens[0]] + resto if resto is not None and 0 <= resto < 100 else None
    if len(tokens) == 1:
        return _CORTOS.get(tokens[0], _DECENAS.get(tokens[0]))
    if len(tokens) == 3 and tokens[0] in _DECENAS and tokens[1] == "y":
        unidad = _CORTOS.get(tokens[2], 0)
        if 1 <= unidad <= 9:
            return _DECENAS[tokens[0]] + unidad
    return None


def interpretar_numero(texto: str, decimal_implicito: bool = False) -> float | None:
    if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", texto):
        if re.search(r"[.,]\d{3}$", texto):
            return None  # Separador decimal o de miles.
        valor = float(texto.replace(",", "."))
        return valor if math.isfinite(valor) else None
    tokens = texto.split()
    if tokens[-2:] == ["y", "medio"]:
        entero = _entero(tokens[:-2])
        return entero + 0.5 if entero is not None else None
    for indice, token in enumerate(tokens):
        if token in {"con", "coma", "punto"}:
            entero = _entero(tokens[:indice])
            decimales = [_entero([t]) for t in tokens[indice + 1:]]
            if entero is not None and decimales and all(d is not None and 0 <= d < 10 for d in decimales):
                return float(f"{entero}.{''.join(str(d) for d in decimales)}")
            return None
    entero = _entero(tokens)
    if entero is not None:
        return float(entero)
    if decimal_implicito and len(tokens) == 2:
        primero, segundo = (_CORTOS.get(t) for t in tokens)
        if primero is not None and segundo is not None and 0 <= segundo < 10:
            return primero + segundo / 10
    return None


def _sensores(catalogo):
    return {(x["tag"], x["variable"]): x["unidad"] for x in catalogo.get("sensores", [])}


def _fila(clausula, equipo, sensores, candidatos=()):
    clausula = re.sub(r"(\b(?:me dio|medio)\s+0)-(\d)(?=\s+de diferencial\b)", r"\1.\2", clausula)
    numeros = list(_NUMEROS.finditer(clausula))
    for variable, patron in _CUALITATIVAS:
        if re.search(patron, clausula) and all(n.group() in {"un", "una"} for n in numeros):
            if equipo is None:
                if len(candidatos) > 1 and not _INCERTIDUMBRE.search(clausula):
                    return None, {
                        "tipo": "equipo", "clausula": clausula, "opciones": [e["tag"] for e in candidatos],
                    }
                return None, True
            return FilaParte(tag=equipo["tag"], variable=variable, observacion=clausula), False

    candidatas = [
        (variable, unidades) for variable, tipo, patron, unidades in _CUANTITATIVAS
        if re.search(patron, clausula) and (equipo is None or equipo.get("tipo") == tipo)
    ]
    lectura_simple = re.fullmatch(r"(?:esta\s+)?en\s+.+", clausula)
    if (
        equipo is None and len(candidatos) > 1 and len(numeros) == 1
        and not _INCERTIDUMBRE.search(clausula) and (candidatas or lectura_simple)
    ):
        tipos = {tipo for _, tipo, patron, _ in _CUANTITATIVAS if re.search(patron, clausula)}
        opciones = [e["tag"] for e in candidatos if not tipos or e.get("tipo") in tipos]
        if len(opciones) > 1:
            return None, {"tipo": "equipo", "clausula": clausula, "opciones": opciones}
    if not candidatas:
        return None, bool(numeros or any(re.search(p, clausula) for _, _, p, _ in _CUANTITATIVAS))
    if equipo is None or len(candidatas) != 1 or len(numeros) != 1 or _INCERTIDUMBRE.search(clausula):
        return None, True

    variable, permitidas = candidatas[0]
    dicho = numeros[0].group()
    valor = interpretar_numero(dicho, variable in {"presion_diferencial", "nivel"})
    if valor is None:
        return None, True

    explicitas = {_UNIDADES[m.group()] for m in _UNIDAD.finditer(clausula)}
    unidad = None
    if len(explicitas) == 1:
        unidad = next(iter(explicitas))
    elif not explicitas:
        sufijo = clausula[numeros[0].end():].strip()
        if sufijo and not re.match(r"(?:de|y)\b", sufijo):
            return None, True
        if variable == "presion_descarga_local":
            unidad = equipo.get("manometro_local", {}).get("unidad")
        else:
            unidad = sensores.get((equipo["tag"], variable))
    lectura = {
        "tag": equipo["tag"], "variable": variable, "valor": valor,
        "unidad": unidad if unidad in permitidas else None, "clausula": clausula,
    }
    decimal_explicito = re.search(r"[.,]|\b(?:con|coma|punto)\b", dicho)
    if variable == "presion_diferencial" and valor >= 10 and valor.is_integer() and not decimal_explicito:
        if valor < 100:
            return None, lectura | {"tipo": "valor", "alternativas": [valor, valor / 10]}
        return None, True
    if lectura["unidad"] is None:
        return None, lectura | {"tipo": "unidad"}
    return FilaParte(tag=equipo["tag"], variable=variable, valor=valor, unidad=unidad), False


def lectura_del_equipo(clausula: str, equipo: dict, catalogo: dict):
    return _fila(clausula, equipo, _sensores(catalogo))


def unidad_de_variable(variable: str, unidad: str) -> bool:
    return any(codigo == variable and unidad in permitidas
               for codigo, _, _, permitidas in _CUANTITATIVAS)


def indexar_aliases(catalogo: dict) -> dict:
    """Agrupa los equipos por tag o alias. Conserva los alias compartidos."""
    alias = {}
    for equipo in catalogo["equipos"]:
        for nombre in [equipo["tag"], *equipo.get("nombres", [])]:
            alias.setdefault(normalizar_texto(nombre), {})[equipo["tag"]] = equipo
    return alias


def normalizar_unidad(texto: str) -> str | None:
    return _UNIDADES.get(normalizar_texto(texto.strip()))


def unidad_compatible(anterior: str | None, nueva: str) -> bool:
    return anterior == nueva or (anterior in _PRESIONES and nueva in _PRESIONES)


def interpretar_medicion(
    texto: str, variable: str, *, valor_explicito: bool = False
) -> tuple[float, str | None] | None:
    """Lee una medición completa de una corrección, con unidad opcional."""
    unidades = list(_UNIDAD.finditer(texto))
    if len(unidades) > 1 or (unidades and unidades[0].end() != len(texto)):
        return None
    unidad = None
    if unidades:
        unidad = _UNIDADES[unidades[0].group()]
        texto = texto[:unidades[0].start()].strip()
    valor = interpretar_numero(texto, variable in {"presion_diferencial", "nivel"})
    if valor is None or (not valor_explicito and variable == "presion_diferencial"
                         and valor >= 10 and valor.is_integer()):
        return None
    return valor, unidad


def extraer_parte(texto: str, catalogo: dict) -> ResultadoExtraccion:
    """Devuelve filas inequívocas y señala lecturas que requieren revisión."""
    alias = indexar_aliases(catalogo)
    patrones = [re.escape(a) for a in sorted(alias, key=len, reverse=True)]
    patrones.extend([r"[a-z]{1,3}-\d{2,4}[a-z]?", "la bomba", "el filtro"])
    referencias = re.compile(r"(?<![\w-])(" + "|".join(patrones) + r")(?![\w-])")
    sensores = _sensores(catalogo)
    filas = {}
    conflictos = set()
    dudas = []
    no_interpretada = False
    bombas_mencionadas = {}
    bomba_sin_identificar = False

    for frase in _FRASES.split(normalizar_texto(texto)):
        equipo = None
        candidatos = []
        for indice, fragmento in enumerate(referencias.split(frase)):
            if indice % 2:
                equipos = alias.get(fragmento, {})
                candidatos = list(equipos.values())
                if not candidatos and fragmento in {"la bomba", "el filtro"}:
                    candidatos = [e for e in catalogo["equipos"] if e["tipo"] == fragmento.split()[1]]
                equipo = next(iter(equipos.values())) if len(equipos) == 1 else None
                if equipo and equipo["tipo"] == "bomba":
                    bombas_mencionadas[equipo["tag"]] = equipo
                elif fragmento == "la bomba" or fragmento.startswith("b-") or any(
                    candidato["tipo"] == "bomba" for candidato in candidatos
                ):
                    bomba_sin_identificar = True
                continue
            for clausula in _CLAUSULAS.split(fragmento):
                opciones = candidatos
                equipo_clausula = equipo
                if indice == 0 and re.search(r"\bdiferencial\b", clausula):
                    opciones = [e for e in catalogo["equipos"] if e.get("tipo") == "filtro"]
                if (indice == 0 and len(bombas_mencionadas) == 1 and not bomba_sin_identificar
                        and re.match(r"\s*(?:el )?rodamiento\b", clausula)):
                    equipo_clausula = next(iter(bombas_mencionadas.values()))
                fila, duda = _fila(clausula.strip(), equipo_clausula, sensores, opciones)
                if isinstance(duda, dict):
                    if duda not in dudas:
                        dudas.append(duda)
                elif duda:
                    no_interpretada = True
                if fila is None:
                    continue
                clave = (fila.tag, fila.variable)
                if clave in conflictos:
                    continue
                if clave in filas and filas[clave] != fila:
                    del filas[clave]
                    conflictos.add(clave)
                    no_interpretada = True
                else:
                    filas[clave] = fila

    return ResultadoExtraccion(list(filas.values()), bool(dudas or no_interpretada), dudas, no_interpretada)
