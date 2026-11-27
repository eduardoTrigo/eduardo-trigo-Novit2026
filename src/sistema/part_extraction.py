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
_NUMERAL = rf"(?:[+-]?\d+(?:[.,]\d+)*|{_PALABRAS})\b"
_NUMEROS = re.compile(
    rf"(?<![\w-])(?:menos\s+)?{_NUMERAL}(?:\s+(?:(?:y|con|coma|punto)\s+)?{_NUMERAL})*"
    rf"(?:\s+y\s+medio\b)?"
)
_FRASES = re.compile(r"[!?;\n]+|(?<!\d)\.|\.(?!\d)")
_TOMA_FOTO = re.compile(
    r"(?:^|,|\by\b)\s*(?:(?:le\s+)?(?:saque|tome|adjunte|dejo)\s+(?:(?:una|otra)\s+)?fotos?\b|"
    r"fotos?\b(?=\s+d(?:e|el)\b))"
)
_PREDICADO = (
    r"(?:(?:que\s+)?(?:no\s+)?(?:tiene|esta|estaba|arranca|parad[ao]|disparad[ao]|"
    r"dio|medio|marco|gotea|pierde|vibra|suena|hace)\b|[+-]?\d)"
)
_COMPONENTE = r"(?:el\s+)?(?:rodamiento|motor)\b"
_LECTURA_COMPONENTE = re.compile(
    rf"{_COMPONENTE}\s+(?:{_PREDICADO}|(?:no\s+)?(?:midio|mide|me\s+dio|se\s+midio|marca)\b"
    rf"|(?:(?:menos|aproximadamente|entre)\s+)?{_NUMERAL})"
)
_LECTURA_TRAS_FOTO = re.compile(rf"\s*(?:{_PREDICADO}|{_LECTURA_COMPONENTE.pattern}|(?:menos\s+)?{_NUMERAL})")
_CORTE_FOTO = re.compile(rf"(?<!\d),|,(?!\d)|\by\s+(?={_PREDICADO}|{_LECTURA_COMPONENTE.pattern})")
_UBICACION_FOTO = re.compile(
    r"\b(?:(?:cerca|al\s+lado|detras|delante|debajo|encima|alrededor|enfrente|"
    r"en\s+las\s+inmediaciones)\s+del?|(?:junto|frente|proxim[oa])\s+al?|donde)\s+[^,]*"
)
_COORDINACION_ESPACIAL = re.compile(r"\b(?:y|e|o|u|ni)\b")
_CLAUSULAS = re.compile(
    r"(?<!\bmenos)(?:(?<!\d),|,(?!\d))|\by\s+(?=(?:(?:el|la|las)\s+)?"
    r"(?:temperatura|presion|descarga|diferencial|nivel|correas|"
    r"gotea|pierde|sigue goteando|visor|hace|tiene|ruido|vibra|vibracion)\b)"
)
_CORTE_COMPONENTE = re.compile(rf"\s+(?:y\s+)?(?={_COMPONENTE})")
_ENLACE_COMPONENTE = re.compile(r"\b(?:a|al|bajo|con|de|del|desde|el|en|entre|hacia|hasta|o|para|por|sin|sobre|tras|u)$")
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
_PRESION_EN_KG = {"kg/cm2": 1.0, "psi": 1 / 14.2233, "bar": 1 / 0.980665}
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
    ("estado", r"\b(?:parad[ao]|disparad[ao]|no arranca)\b"),
    ("goteo_empaquetadura", r"\b(?:gotea|goteando|goteo|pierde|chorrea)\b.*\bempaquetadura\b|"
                            r"\bempaquetadura\b.*\b(?:gotea|goteando|goteo|pierde|chorrea)\b"),
    ("ruido", r"\b(?:golpeteo|ruido|suena)\b"),
    ("vibracion", r"\b(?:vibracion|vibra|vibrando)\b"),
)


@dataclass(frozen=True)
class ResultadoExtraccion:
    filas: list[FilaParte]
    pendiente: bool
    dudas: list[dict] = field(default_factory=list)
    no_interpretada: bool = False
    segmentos_no_interpretados: list[dict] = field(default_factory=list)


def normalizar_texto(texto: str) -> str:
    return "".join(
        letra for letra in unicodedata.normalize("NFKD", texto.casefold())
        if not unicodedata.combining(letra)
    )


def excluir_referencias_espaciales(texto: str, referencias: re.Pattern) -> str | None:
    """Excluye ubicaciones. Devuelve None cuando no se puede delimitar su alcance."""
    otro_lugar = re.compile(r"\s*(?:(?:de|del|a|al)\s+)?" + referencias.pattern + r"\s*")
    for lugar in _UBICACION_FOTO.finditer(texto):
        principal, *coordinados = _COORDINACION_ESPACIAL.split(lugar[0])
        if len(referencias.findall(principal)) > 1 or any(not otro_lugar.fullmatch(tramo) for tramo in coordinados):
            return None
    return _UBICACION_FOTO.sub(" ", texto)


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
    if texto.startswith("menos "):
        magnitud = texto[6:].lstrip()
        if magnitud.startswith(("+", "-", "menos ")):
            return None
        valor = interpretar_numero(magnitud, decimal_implicito)
        return -valor if valor is not None else None
    grupos = re.fullmatch(r"[+-]?\d{1,3}(?:(?:\.\d{3})+)(?:,\d+)?", texto)
    grupos_ingles = re.fullmatch(r"[+-]?\d{1,3}(?:(?:,\d{3})+)(?:\.\d+)?", texto)
    if grupos and ("," in texto or texto.count(".") > 1):
        texto = texto.replace(".", "")
    elif grupos_ingles and ("." in texto or texto.count(",") > 1):
        texto = texto.replace(",", "")
    elif re.fullmatch(r"[+-]?\d+[.,]\d{3}", texto):
        return None  # Un único separador puede ser decimal o de miles.
    if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", texto):
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
        primero, segundo = (_entero([t]) for t in tokens)
        if primero is not None and segundo is not None and 0 <= segundo < 10:
            return primero + (-segundo if tokens[0].startswith("-") else segundo) / 10
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
    numero = numeros[0]
    if re.search(r"\bmenos\b", clausula[:numero.start()] + " " + clausula[numero.end():]):
        return None, True
    dicho = numero.group()
    valor = interpretar_numero(dicho, variable in {"presion_diferencial", "nivel"})
    if valor is None:
        return None, True

    explicitas = {_UNIDADES[m.group()] for m in _UNIDAD.finditer(clausula)}
    unidad = None
    if len(explicitas) == 1:
        unidad = next(iter(explicitas))
    elif not explicitas:
        sufijo = clausula[numeros[0].end():].strip()
        patron_variable = next(p for v, _, p, _ in _CUANTITATIVAS if v == variable)
        if not sufijo or re.match(r"(?:de\b|y\s+medio\b)", sufijo) or re.match(patron_variable, sufijo):
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


def convertir_presion(valor: float, origen: str, destino: str) -> float | None:
    if origen in _PRESION_EN_KG and destino in _PRESION_EN_KG:
        return valor * _PRESION_EN_KG[origen] / _PRESION_EN_KG[destino]
    return None


def lecturas_equivalentes(primera: FilaParte, segunda: FilaParte) -> bool:
    if primera == segunda:
        return True
    if primera.valor is None or segunda.valor is None or primera.unidad == segunda.unidad:
        return False
    convertido = convertir_presion(segunda.valor, segunda.unidad, primera.unidad)
    return convertido is not None and math.isclose(primera.valor, convertido, rel_tol=1e-6)


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


def _fragmentos_de_oracion(oracion, referencias):
    """Devuelve texto de lectura y, tras una foto, sus referencias directas de equipo."""
    tomas = list(_TOMA_FOTO.finditer(oracion))
    if not tomas:
        yield oracion, None
        return
    yield oracion[:tomas[0].start()].strip(), None
    alias = referencias.pattern
    sujeto_foto = re.compile(rf"\b(?:de|del|a)\s+(?P<sujeto>{alias}(?:\s+(?:y|e|o|u|/)\s+{alias})*)\s*$")
    corte_foto = re.compile(rf"{_CORTE_FOTO.pattern}|\by\s+(?={alias}{_LECTURA_TRAS_FOTO.pattern})")
    sujetos = []
    for indice, toma in enumerate(tomas):
        fin = tomas[indice + 1].start() if indice + 1 < len(tomas) else len(oracion)
        complemento = oracion[toma.end():fin]
        corte = next((m for m in corte_foto.finditer(complemento)
                      if not _UBICACION_FOTO.match(complemento[m.end():].lstrip())), None)
        if corte is not None:
            referencia, lectura = complemento[:corte.start()], complemento[corte.end():].strip()
        else:
            corte = next((m.end() for m in referencias.finditer(complemento)
                          if _LECTURA_TRAS_FOTO.match(complemento, m.end())), len(complemento))
            referencia, lectura = complemento[:corte], complemento[corte:].strip()
        directa = excluir_referencias_espaciales(referencia, referencias)
        if directa and (sujeto := sujeto_foto.search(directa.rstrip(" ,"))):
            sujetos.extend(referencias.findall(sujeto["sujeto"]))
        if lectura:
            yield lectura, sujetos
            sujetos = []


def _clausulas_del_equipo(fragmento):
    for clausula in _CLAUSULAS.split(fragmento):
        inicio = 0
        for corte in _CORTE_COMPONENTE.finditer(clausula):
            anterior = clausula[inicio:corte.start()].strip()
            if not anterior or _ENLACE_COMPONENTE.search(anterior):
                continue
            # Tras una lectura numérica, un segundo componente puede quedar
            # pendiente aunque la transcripción haya omitido su medición.
            if not _LECTURA_COMPONENTE.match(clausula, corte.end()) and (
                not _NUMEROS.search(anterior) or any(re.search(p, anterior) for _, p in _CUALITATIVAS)
            ):
                continue
            yield anterior
            inicio = corte.end()
        yield clausula[inicio:].strip()


def _contexto_tras_foto(nombres, buscar, alias, equipo, candidatos):
    # Una foto puede aportar contexto inicial. Si ya había equipo, sus candidatos
    # siguen vigentes y otro objetivo explícito introduce duda en vez de sustituirlos.
    sugeridos = {e["tag"]: e for n in nombres for e in buscar(n) if candidatos is None or n in alias}
    if candidatos is None and sugeridos:
        opciones = list(sugeridos.values())
        return opciones[0] if len(opciones) == 1 else None, opciones
    if candidatos and sugeridos.keys() - {e["tag"] for e in candidatos}:
        return None, list({e["tag"]: e for e in [*candidatos, *sugeridos.values()]}.values())
    return equipo, candidatos


def _clausulas_del_parte(texto, catalogo):
    alias = indexar_aliases(catalogo)
    patrones = [re.escape(a) for a in sorted(alias, key=len, reverse=True)]
    genericos = {"la bomba": "bomba", "el filtro": "filtro", "bomba": "bomba", "filtro": "filtro"}
    patrones.extend([r"[a-z]{1,3}-\d{2,4}[a-z]?", "la bomba", "el filtro",
                     r"(?:bomba|filtro)(?=\s+(?:esta|estar|tiene|medio|me dio|gotea|vibra)\b)"])
    referencias = re.compile(r"(?<![\w-])(" + "|".join(patrones) + r")(?![\w-])")

    def buscar(nombre):
        if nombre in alias:
            return list(alias[nombre].values())
        tipo = genericos.get(nombre)
        return [e for e in catalogo["equipos"] if e["tipo"] == tipo] if tipo else []

    bombas_mencionadas = {}
    bomba_sin_identificar = False
    for oracion in _FRASES.split(texto):
        equipo = None
        candidatos = None
        for frase, sujetos_foto in _fragmentos_de_oracion(oracion, referencias):
            if sujetos_foto and not referencias.match(frase):
                equipo, candidatos = _contexto_tras_foto(sujetos_foto, buscar, alias, equipo, candidatos)
            fragmentos = referencias.split(frase)
            for indice, fragmento in enumerate(fragmentos):
                if indice % 2:
                    equipos = buscar(fragmento)
                    if indice > 1 and re.fullmatch(r"\s*(?:y|e|o|u|/)\s*", fragmentos[indice - 1]):
                        equipos = list({e["tag"]: e for e in (*candidatos, *equipos)}.values())
                    candidatos = equipos
                    equipo = equipos[0] if len(equipos) == 1 and fragmento in alias else None
                    if equipo and equipo["tipo"] == "bomba":
                        bombas_mencionadas[equipo["tag"]] = equipo
                    elif genericos.get(fragmento) == "bomba" or fragmento.startswith("b-") or any(
                        candidato["tipo"] == "bomba" for candidato in candidatos
                    ):
                        bomba_sin_identificar = True
                    continue
                for clausula in _clausulas_del_equipo(fragmento):
                    opciones = candidatos or []
                    equipo_clausula = equipo
                    if candidatos is None and re.search(r"\bdiferencial\b", clausula):
                        opciones = [e for e in catalogo["equipos"] if e.get("tipo") == "filtro"]
                    if (candidatos is None and sujetos_foto is None and len(bombas_mencionadas) == 1
                            and not bomba_sin_identificar and re.match(r"(?:el\s+)?rodamiento\b", clausula)):
                        equipo_clausula = next(iter(bombas_mencionadas.values()))
                    yield clausula, equipo_clausula, opciones


def extraer_parte(texto: str, catalogo: dict) -> ResultadoExtraccion:
    """Devuelve filas inequívocas y señala lecturas que requieren revisión."""
    sensores = _sensores(catalogo)
    filas = {}
    conflictos = {}
    dudas = []
    segmentos = []
    texto = re.sub(r"\bmenos\s+,", "menos,", normalizar_texto(texto))
    # La coma de «parada, en reserva» pertenece a la misma observación de estado.
    texto = re.sub(r"\b(parad[ao]),\s*(en reserva)\b", r"\1 \2", texto)
    for clausula, equipo, candidatos in _clausulas_del_parte(texto, catalogo):
        fila, duda = _fila(clausula, equipo, sensores, candidatos)
        if isinstance(duda, dict):
            if duda not in dudas:
                dudas.append(duda)
        elif duda:
            segmentos.append({"texto": clausula, "motivo": "lectura_no_resuelta"})
        if fila is None:
            continue
        clave = (fila.tag, fila.variable)
        if clave not in filas and clave not in conflictos:
            filas[clave] = fila
            continue
        anteriores = conflictos.get(clave) or [filas[clave]]
        if not any(lecturas_equivalentes(anterior, fila) for anterior in anteriores):
            filas.pop(clave, None)
            conflictos[clave] = [*anteriores, fila]

    for (tag, variable), alternativas in conflictos.items():
        if all(f.valor is not None for f in alternativas):
            dudas.append(duda_por_conflicto(alternativas))
        else:
            segmentos.extend({"texto": f.observacion or "", "motivo": "observaciones_en_conflicto",
                              "tag": tag, "variable": variable} for f in alternativas)
    return ResultadoExtraccion(list(filas.values()), bool(dudas or segmentos), dudas, bool(segmentos), segmentos)


def duda_por_conflicto(alternativas: list[FilaParte]) -> dict:
    fila = alternativas[0]
    return {"tipo": "conflicto", "tag": fila.tag, "variable": fila.variable,
            "alternativas": [f.model_dump() for f in alternativas]}
