"""Catálogo, rangos históricos y límites publicados por la planta."""

import math
from datetime import datetime, timedelta

import httpx
from planta_contrato import FilaParte


_VENTANA = timedelta(days=45)
_PRESION_EN_KG = {"kg/cm2": 1.0, "psi": 1 / 14.2233, "bar": 1 / 0.980665}
_VARIABLE_SENSOR = {"presion_descarga_local": "presion_descarga"}


class ClientePlanta:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._catalogo = None

    def catalogo(self) -> dict:
        if self._catalogo is None:
            respuesta = httpx.get(f"{self.url}/api/planta", timeout=2.0)
            respuesta.raise_for_status()
            datos = respuesta.json()
            if not isinstance(datos, dict) or not isinstance(datos.get("equipos"), list):
                raise ValueError("Catálogo de equipos inválido")
            if not datos["equipos"]:
                raise ValueError("Catálogo de equipos vacío")
            if not isinstance(datos.get("sensores", []), list):
                raise ValueError("Catálogo de sensores inválido")
            self._catalogo = datos

        return self._catalogo

    def historial(self, tag: str, variable: str, hasta: datetime) -> list[dict]:
        if hasta.utcoffset() is None:
            raise ValueError("El historian requiere fechas con zona horaria")
        parametros = {"desde": (hasta - _VENTANA).isoformat(), "hasta": hasta.isoformat()}
        url = f"{self.url}/api/historian/{tag}/{variable}"
        for intento in range(2):
            try:
                respuesta = httpx.get(url, params=parametros, timeout=2.0)
                respuesta.raise_for_status()
                muestras = respuesta.json()
                if not isinstance(muestras, list):
                    raise ValueError("Respuesta del historian inválida")
                return muestras
            except (httpx.TimeoutException, httpx.NetworkError):
                if intento == 1:
                    raise


def _finito(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _rango(valor, minimo, maximo):
    fuera = (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo)
    return {"estado": "fuera_de_rango" if fuera else "compatible", "minimo": minimo, "maximo": maximo}


def _historico(muestras, valor, hasta):
    validas = {}
    for muestra in muestras:
        if not isinstance(muestra, dict) or not _finito(muestra.get("valor")):
            continue
        try:
            fecha = datetime.fromisoformat(muestra["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if fecha.utcoffset() is not None and hasta - _VENTANA <= fecha <= hasta:
            validas[fecha] = muestra["valor"]
    if len(validas) < 2:
        return {"estado": "insuficiente" if muestras else "vacio", "muestras": len(validas)}
    return _rango(valor, min(validas.values()), max(validas.values())) | {"muestras": len(validas)}


def verificar_lectura(fila: FilaParte, ts: datetime, catalogo: dict, historial) -> dict:
    variable = _VARIABLE_SENSOR.get(fila.variable, fila.variable)
    sensor = next((s for s in catalogo.get("sensores", [])
                   if (s["tag"], s["variable"]) == (fila.tag, variable)), None)
    evidencia = {"fila": fila.model_dump(), "variable_sensor": variable,
                 "historico": {"estado": "sin_sensor"}, "limites": {"estado": "sin_fuente"}}
    if sensor is None:
        return evidencia

    unidad = sensor["unidad"]
    if fila.unidad == unidad:
        valor = fila.valor
    elif fila.unidad in _PRESION_EN_KG and unidad in _PRESION_EN_KG:
        valor = fila.valor * _PRESION_EN_KG[fila.unidad] / _PRESION_EN_KG[unidad]
    else:
        evidencia["historico"] = {"estado": "unidad_incompatible"}
        return evidencia

    evidencia.update(comparado=valor, unidad=unidad)
    baja, alta = sensor.get("alarma_baja"), sensor.get("alarma_alta")
    baja = baja if _finito(baja) else None
    alta = alta if _finito(alta) else None
    if (baja is not None or alta is not None) and not (baja is not None and alta is not None and baja > alta):
        evidencia["limites"] = _rango(valor, baja, alta)

    try:
        if ts.utcoffset() is None:
            raise ValueError("Fecha de nota sin zona horaria")
        evidencia["historico"] = _historico(historial(fila.tag, variable, ts), valor, ts)
    except (httpx.HTTPError, ValueError):
        evidencia["historico"] = {"estado": "inaccesible"}
    return evidencia
