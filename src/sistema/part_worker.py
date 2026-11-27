"""Deriva borradores y aplica respuestas de tablet fuera del hilo MQTT."""

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from planta_contrato import RespuestaOperador

from sistema.audio_service import descargar_audio
from sistema.execution_budget import ControlEjecucion, CorteEjecucion, PresupuestoEjecucion
from sistema.part_store import AlmacenPartes, ParteGuardado, TareaSTT
from sistema.transcription_service import MAX_BYTES_RESPUESTA

logger = logging.getLogger(__name__)


class WorkerPartes:
    def __init__(
        self, almacen: AlmacenPartes, catalogo: Callable[[], dict], *, historial: Callable | None = None,
        transcribir_respuesta: Callable[[bytes], str | None] | None = None,
        hay_seguridad: Callable[[], bool] = lambda: False,
        presupuesto: PresupuestoEjecucion | None = None, reloj: Callable[[], float] = time.monotonic,
        fuentes_con_timeout: bool = False, modelo=None, foto: Callable | None = None,
    ):
        self.almacen = almacen
        self.catalogo = catalogo
        self.historial = historial
        self.transcribir_respuesta = transcribir_respuesta
        self.hay_seguridad = hay_seguridad
        self.presupuesto = presupuesto or PresupuestoEjecucion()
        self.reloj = reloj
        self._fuentes_con_timeout = fuentes_con_timeout
        self._modelo = modelo
        self._foto = foto
        self._agente = None
        self.detencion = threading.Event()
        self.hilo: threading.Thread | None = None

    @property
    def agente(self):
        if self._agente is None:
            from sistema.part_agent import AgentePartes

            historial = (lambda *args, **kw: self.historial(*args, **kw)) if self.historial is not None else None
            self._agente = AgentePartes(lambda **kw: self.catalogo(**kw), historial,
                                       fuentes_con_timeout=self._fuentes_con_timeout, modelo=self._modelo,
                                       foto=self._foto)
        return self._agente

    def procesar_siguiente(self) -> bool:
        inicio = self.reloj()
        pendiente = self.almacen.siguiente_respuesta(timeout=self.presupuesto.reserva_cierre_s)
        if pendiente is not None:
            return self._procesar_respuesta(*pendiente, inicio=inicio)
        if self.transcribir_respuesta is not None and not self.hay_seguridad():
            tarea_stt = self.almacen.reclamar_stt()
            if tarea_stt is not None:
                return self._procesar_stt(tarea_stt, self.transcribir_respuesta)

        inicio = self.reloj()
        tarea = self.almacen.siguiente(timeout=self.presupuesto.reserva_cierre_s)
        if tarea is None:
            return False

        nota, texto = tarea
        return self._ejecutar_turno(nota, texto or "", inicio=inicio)

    def _ejecutar_turno(self, nota, texto, *, inicio, parte=None, respuesta=None, fase=None):
        reserva = self.almacen.reclamar_ejecucion(nota.id, respuesta.mensaje_id if respuesta else None,
                                                self.presupuesto)
        if reserva is None:
            return False
        identificador, previo, recuperacion = reserva
        control = ControlEjecucion(nota.id, identificador, self.presupuesto, previo, inicio=inicio,
                                   reloj=self.reloj, cancelada=self.detencion.is_set)
        try:
            if recuperacion == "interrumpida":
                raise CorteEjecucion("ejecucion_interrumpida")
            control.verificar(tiempo=self.presupuesto.reserva_cierre_s)
            if recuperacion == "reversion" and not control.reintentar():
                raise CorteEjecucion("reintentos_agotados")
            plan = self.agente.nota(control, nota, texto) if respuesta is None else self.agente.respuesta(
                control, parte, respuesta, fase,
            )
            estado = plan.estado or (parte.estado if parte else "revision_manual")
            estado = "espera_operador" if estado in {"borrador_pendiente", "aclaracion_pendiente"} else estado
            if plan.resultado == "requiere_stt":
                estado = "espera_stt"
            control.iniciar_cierre(estado)
            if respuesta is None:
                aplicado = self.almacen.guardar(nota, plan.estado, plan.filas, plan.mensaje,
                                                plan.contexto, ejecucion=control, archivos_fotos=plan.archivos_fotos)
            else:
                aplicado = self.almacen.aplicar_respuesta(
                    respuesta, plan.resultado, plan.mensaje, nuevo_estado=plan.estado, filas=plan.filas,
                    contexto=plan.contexto, fase=fase, ejecucion=control,
                )
        except sqlite3.IntegrityError:
            self.almacen.registrar_reversion(control)
            control.traza("cierre", "persistencia_revertida")
            raise
        except (CorteEjecucion, ImportError, ValueError, sqlite3.OperationalError) as error:
            if isinstance(error, CorteEjecucion) and error.motivo == "reserva_perdida":
                return False
            if isinstance(error, CorteEjecucion):
                motivo = error.motivo
            elif isinstance(error, ImportError):
                motivo = "runtime_no_disponible"
            elif isinstance(error, sqlite3.OperationalError):
                motivo = "timeout_global" if control.restante() <= 0 else "fuente_no_disponible"
            else:
                motivo = "salida_invalida"
            control.iniciar_cierre("revision_manual", motivo)
            if respuesta is None:
                aplicado = self.almacen.guardar(nota, "revision_manual", [], None, ejecucion=control)
            else:
                aplicado = self.almacen.aplicar_respuesta(
                    respuesta, "corte_ejecucion", nuevo_estado="revision_manual", fase=fase, ejecucion=control,
                )
        control.medir()
        control.traza("cierre", "persistido" if aplicado else "sin_cambios")
        return aplicado

    def _parte_vigente(self, respuesta: RespuestaOperador) -> ParteGuardado | None:
        parte = self.almacen.buscar_por_mensaje(respuesta.mensaje_id, timeout=self.presupuesto.reserva_cierre_s)
        if (parte is None or parte.mensaje_id != respuesta.mensaje_id
                or parte.estado not in {"borrador_pendiente", "aclaracion_pendiente"}):
            return None
        return parte

    def _procesar_stt(self, tarea: TareaSTT, transcribir: Callable[[bytes], str | None]) -> bool:
        respuesta = tarea.respuesta
        if self._parte_vigente(respuesta) is None:
            return self.almacen.aplicar_respuesta(respuesta, "insuficiente", fase="stt_procesando")
        try:
            if not respuesta.audio_url:
                raise ValueError("La respuesta de voz carece de la ruta de audio")
            audio = descargar_audio(respuesta.audio_url, plazo=2.0, limite_bytes=MAX_BYTES_RESPUESTA)
            texto = transcribir(audio)
            if texto is not None and not texto.strip():
                raise ValueError("La transcripción de tablet está vacía")
        except Exception as error:
            return self.almacen.finalizar_stt(tarea, error=error)
        return self.almacen.finalizar_stt(tarea, texto=texto)

    def _procesar_respuesta(self, respuesta: RespuestaOperador, fase: str | None, *, inicio) -> bool:
        parte = self._parte_vigente(respuesta)
        if parte is None or self.almacen.respuesta_ignorable(respuesta, fase, timeout=self.presupuesto.reserva_cierre_s):
            return self.almacen.aplicar_respuesta(respuesta, "insuficiente", fase=fase)
        return self._ejecutar_turno(parte.nota, respuesta.texto, inicio=inicio, parte=parte, respuesta=respuesta, fase=fase)

    def iniciar(self) -> None:
        if self.hilo is not None:
            raise RuntimeError("Worker de partes ya iniciado")
        self.hilo = threading.Thread(target=self._ejecutar, name="pia3-partes", daemon=True)
        self.hilo.start()

    def _ejecutar(self) -> None:
        while not self.detencion.is_set():
            try:
                if not self.procesar_siguiente():
                    self.detencion.wait(0.5)
            except Exception:
                logger.exception("Error al derivar un parte de recorrida")
                self.detencion.wait(2.0)

    def detener(self) -> bool:
        self.detencion.set()
        if self.hilo is None:
            return True
        self.hilo.join(timeout=5)
        return not self.hilo.is_alive()
