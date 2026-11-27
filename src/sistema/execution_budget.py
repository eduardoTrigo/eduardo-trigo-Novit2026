"""Presupuesto por nota, deadline monotónico y aislamiento de llamadas sin escritura."""

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


logger = logging.getLogger(__name__)
Conteo = Annotated[int, Field(ge=0)]


class PresupuestoEjecucion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)

    pasos: Annotated[int, Field(gt=0)] = 48
    tiempo_s: Annotated[float, Field(gt=0, le=60)] = 10.0
    reserva_cierre_s: Annotated[float, Field(gt=0, le=1)] = 0.25
    tokens_contexto: Annotated[int, Field(gt=0)] = 4200
    tokens_entrada: Conteo = 16800
    tokens_salida: Conteo = 3600
    reserva_salida: Annotated[int, Field(gt=0)] = 900
    reintentos: Conteo = 1

    @model_validator(mode="after")
    def reserva_valida(self):
        if self.reserva_cierre_s >= self.tiempo_s:
            raise ValueError("La reserva de cierre debe ser menor que el tiempo activo")
        return self


class ConsumoEjecucion(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)

    pasos: Conteo = 0
    activo_ms: Conteo = 0
    tokens_contexto: Conteo = 0
    tokens_entrada: Conteo = 0
    tokens_salida: Conteo = 0
    tokens_entrada_reales: Conteo | None = None
    tokens_salida_reales: Conteo | None = None
    intentos_modelo: Conteo = 0
    usos_modelo_medidos: Conteo = 0
    reintentos: Conteo = 0
    interacciones: Conteo = 0


class CorteEjecucion(Exception):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


class AisladorLecturas:
    """Una llamada en vuelo. El worker descarta entregas tardías de operaciones sin escrituras."""

    def __init__(self):
        self._cupo = threading.Lock()

    def invocar(self, operacion, control):
        if not self._cupo.acquire(blocking=False):
            raise CorteEjecucion("fuente_no_disponible")
        final = threading.Event()
        resultado = []

        def ejecutar():
            try:
                resultado.append((True, operacion()))
            except BaseException as error:
                resultado.append((False, error))
            finally:
                self._cupo.release()
                final.set()

        try:
            threading.Thread(target=ejecutar, name="pia3-partes-lectura", daemon=True).start()
        except BaseException:
            self._cupo.release()
            raise
        while True:
            control.verificar(tiempo=control.presupuesto.reserva_cierre_s)
            if final.wait(min(0.025, control.plazo_lectura())):
                control.verificar(tiempo=control.presupuesto.reserva_cierre_s)
                exito, valor = resultado[0]
                if not exito:
                    raise valor
                return valor


class ControlEjecucion:
    def __init__(
        self, nota_id: str, ejecucion_id: str, presupuesto: PresupuestoEjecucion,
        previo: ConsumoEjecucion, *, inicio: float, reloj: Callable[[], float], cancelada: Callable[[], bool],
    ):
        self.nota_id = nota_id
        self.id = ejecucion_id
        self.presupuesto = presupuesto
        self.consumo = previo.model_copy()
        self.inicio = inicio
        self.reloj = reloj
        self.cancelada = cancelada
        self._activo_previo = previo.activo_ms
        self.deadline = inicio + max(0.0, presupuesto.tiempo_s - previo.activo_ms / 1000)
        self.estado = "en_curso"
        self.motivo = None
        self.modo = "determinista"
        self.modelo = None
        self.revision_modelo = None
        self.fallback = None

    def restante(self):
        return max(0.0, self.deadline - self.reloj())

    def plazo_lectura(self):
        return max(0.0, self.restante() - self.presupuesto.reserva_cierre_s)

    def verificar(self, *, tiempo=0.0):
        if self.cancelada():
            raise CorteEjecucion("cancelada")
        if self.restante() <= tiempo:
            raise CorteEjecucion("timeout_global")

    def medir(self):
        transcurrido = math.ceil(max(0, self.reloj() - self.inicio) * 1000)
        self.consumo.activo_ms = self._activo_previo + transcurrido

    def paso(self, nombre, operacion, *, aislador=None, antes=None, herramienta=None, argumentos=None):
        self.verificar(tiempo=self.presupuesto.reserva_cierre_s)
        if self.consumo.pasos >= self.presupuesto.pasos - 1:
            raise CorteEjecucion("pasos_agotados")
        self.consumo.pasos += 1
        inicio = self.reloj()
        resultado = "ok"
        try:
            if antes is not None:
                antes()
            valor = operacion() if aislador is None else aislador.invocar(operacion, self)
            self.verificar()
            return valor
        except CorteEjecucion as error:
            resultado = error.motivo
            raise
        except Exception as error:
            resultado = type(error).__name__
            raise
        finally:
            self.medir()
            self.traza(nombre, resultado, inicio=inicio, herramienta=herramienta, argumentos=argumentos)

    def reintentar(self):
        self.verificar(tiempo=self.presupuesto.reserva_cierre_s)
        if self.consumo.reintentos >= self.presupuesto.reintentos:
            return False
        if self.consumo.pasos >= self.presupuesto.pasos - 1:
            raise CorteEjecucion("pasos_agotados")
        self.consumo.reintentos += 1
        return True

    def reservar_modelo(self, entrada):
        if (self.consumo.tokens_entrada + entrada > self.presupuesto.tokens_entrada
                or self.consumo.tokens_salida + self.presupuesto.reserva_salida > self.presupuesto.tokens_salida):
            raise CorteEjecucion("tokens_agotados")
        self.consumo.tokens_entrada += entrada
        self.consumo.tokens_salida += self.presupuesto.reserva_salida
        self.consumo.intentos_modelo += 1

    def ajustar_modelo(self, estimados, entrada, salida):
        datos = self.consumo
        datos.tokens_entrada += entrada - estimados
        datos.tokens_salida += salida - self.presupuesto.reserva_salida
        datos.tokens_entrada_reales = (datos.tokens_entrada_reales or 0) + entrada
        datos.tokens_salida_reales = (datos.tokens_salida_reales or 0) + salida
        datos.usos_modelo_medidos += 1
        if (datos.tokens_entrada > self.presupuesto.tokens_entrada or datos.tokens_salida > self.presupuesto.tokens_salida
                or entrada > self.presupuesto.tokens_contexto or salida > self.presupuesto.reserva_salida):
            raise CorteEjecucion("tokens_agotados")

    def iniciar_cierre(self, estado, motivo=None):
        if motivo is None:
            self.verificar()
        self.estado, self.motivo = estado, motivo
        if self.consumo.pasos < self.presupuesto.pasos and self.restante() > 0:
            self.consumo.pasos += 1
        self.medir()

    def traza(self, etapa, resultado, *, inicio=None, herramienta=None, argumentos=None):
        logger.info("parte_runtime %s", json.dumps({
            "nota_id": self.nota_id, "ejecucion_id": self.id, "etapa": etapa, "resultado": resultado,
            "modo": self.modo, "modelo": self.modelo, "revision_modelo": self.revision_modelo, "costo": None,
            "herramienta": herramienta, "argumentos": argumentos,
            "duracion_ms": round((self.reloj() - (self.inicio if inicio is None else inicio)) * 1000, 2),
            "restante_ms": int(self.restante() * 1000),
            "consumo": self.consumo.model_dump(), "motivo": self.motivo, "fallback": self.fallback,
        }, ensure_ascii=True))
