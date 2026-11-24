"""Deriva borradores y aplica respuestas de tablet fuera del hilo MQTT."""

import logging
import threading
from collections.abc import Callable

from planta_contrato import MensajeTablet, RespuestaOperador

from sistema.audio_service import descargar_audio
from sistema.part_clarification import preguntar, resolver
from sistema.part_context import verificar_lectura
from sistema.part_extraction import extraer_parte
from sistema.part_messages import crear_borrador
from sistema.part_reply import interpretar_respuesta
from sistema.part_store import AlmacenPartes, ParteGuardado, TareaSTT
from sistema.transcription_service import MAX_BYTES_RESPUESTA

logger = logging.getLogger(__name__)


class WorkerPartes:
    def __init__(
        self, almacen: AlmacenPartes, catalogo: Callable[[], dict], *, historial: Callable | None = None,
        transcribir_respuesta: Callable[[bytes], str | None] | None = None,
        hay_seguridad: Callable[[], bool] = lambda: False,
    ):
        self.almacen = almacen
        self.catalogo = catalogo
        self.historial = historial
        self.transcribir_respuesta = transcribir_respuesta
        self.hay_seguridad = hay_seguridad
        self.detencion = threading.Event()
        self.hilo: threading.Thread | None = None

    def procesar_siguiente(self) -> bool:
        pendiente = self.almacen.siguiente_respuesta()
        if pendiente is not None:
            return self._procesar_respuesta(*pendiente)
        if self.transcribir_respuesta is not None and not self.hay_seguridad():
            tarea_stt = self.almacen.reclamar_stt()
            if tarea_stt is not None:
                return self._procesar_stt(tarea_stt, self.transcribir_respuesta)

        tarea = self.almacen.siguiente()
        if tarea is None:
            return False

        nota, texto = tarea
        if not texto:
            return self.almacen.guardar(nota, "revision_manual", [], None)

        catalogo = self.catalogo()
        extraccion = extraer_parte(texto, catalogo)
        filas = extraccion.filas
        if nota.id.startswith("VIVO-"):
            return self.almacen.guardar(nota, "revision_manual", filas, None)
        contexto = {"pendientes": extraccion.dudas, "no_interpretada": extraccion.no_interpretada}
        estado, mensaje = self._preparar_turno(nota, filas, contexto, catalogo, 1)
        return self.almacen.guardar(nota, estado, filas, mensaje, contexto)

    def _preparar_turno(self, nota, filas, contexto, catalogo, version) -> tuple[str, MensajeTablet | None]:
        pendientes = contexto.setdefault("pendientes", [])
        if not pendientes and not contexto.get("no_interpretada") and self.historial is not None:
            evidencia = contexto.setdefault("evidencia", [])
            confirmadas = [r["duda"]["fila"] for r in contexto.get("resoluciones", [])
                           if r["resultado"] == "resuelta" and r.get("duda", {}).get("tipo") == "rango"]
            for fila in filas:
                if fila.valor is None:
                    continue
                datos = fila.model_dump()
                comprobacion = next((e for e in evidencia if e["fila"] == datos), None)
                if comprobacion is None:
                    comprobacion = verificar_lectura(fila, nota.ts, catalogo, self.historial)
                    evidencia.append(comprobacion)
                fuera = any(comprobacion[f]["estado"] == "fuera_de_rango" for f in ("historico", "limites"))
                if fuera and datos not in confirmadas:
                    pendientes.append({"tipo": "rango", "fila": datos})
        if pendientes:
            equipos = {e["tag"]: e for e in catalogo["equipos"]}
            duda = pendientes[0]
            if duda["tipo"] == "valor" and duda["unidad"] is not None:
                duda["confirmar_candidato"] = True
            return "aclaracion_pendiente", preguntar(nota, duda, equipos, filas, version)
        if contexto.get("no_interpretada"):
            return "revision_manual", None
        if not filas:
            return "sin_contenido", None
        if all(fila.valor is None for fila in filas):
            return "revision_manual", None
        return "borrador_pendiente", crear_borrador(nota, filas, version=version)

    def _parte_vigente(self, respuesta: RespuestaOperador) -> ParteGuardado | None:
        parte = self.almacen.buscar_por_mensaje(respuesta.mensaje_id)
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

    def _procesar_respuesta(self, respuesta: RespuestaOperador, fase: str | None) -> bool:
        if fase == "stt_agotado":
            return self.almacen.aplicar_respuesta(respuesta, "stt_fallido", fase=fase)
        parte = self._parte_vigente(respuesta)
        if parte is None:
            return self.almacen.aplicar_respuesta(respuesta, "insuficiente", fase=fase)
        if parte.estado == "aclaracion_pendiente":
            return self._resolver_duda(parte, respuesta, fase)

        resultado, filas = interpretar_respuesta(respuesta.texto, parte.filas, self.catalogo)
        if resultado == "insuficiente" and respuesta.audio_url and fase is None:
            resultado = "requiere_stt"
        if filas is None:
            return self.almacen.aplicar_respuesta(respuesta, resultado, fase=fase)
        catalogo = self.catalogo() if self.historial is not None else {}
        contexto = parte.contexto
        contexto.setdefault("resoluciones", []).append({
            "respuesta_id": respuesta.id, "mensaje_id": respuesta.mensaje_id, "resultado": resultado,
            "filas": [f.model_dump() for f in filas],
        })
        estado, mensaje = self._preparar_turno(parte.nota, filas, contexto, catalogo, parte.version + 1)
        return self.almacen.aplicar_respuesta(
            respuesta, resultado, mensaje, nuevo_estado=estado, filas=filas, contexto=contexto, fase=fase,
        )

    def _resolver_duda(self, parte: ParteGuardado, respuesta: RespuestaOperador, fase: str | None) -> bool:
        resultado, _ = interpretar_respuesta(respuesta.texto, [], self.catalogo)
        if resultado == "rechazada":
            return self.almacen.aplicar_respuesta(respuesta, resultado, fase=fase)
        contexto = parte.contexto
        pendientes = contexto["pendientes"]
        duda = pendientes[0]
        catalogo = self.catalogo()
        resultado, filas, siguiente = resolver(duda, respuesta.texto, catalogo, parte.filas)
        if resultado == "insuficiente":
            if respuesta.audio_url and fase is None:
                resultado = "requiere_stt"
            return self.almacen.aplicar_respuesta(respuesta, resultado, fase=fase)

        contexto.setdefault("resoluciones", []).append({
            "duda": duda, "respuesta_id": respuesta.id, "mensaje_id": respuesta.mensaje_id,
            "resultado": resultado, "filas": [f.model_dump() for f in filas],
        })
        if resultado == "conflicto_lecturas":
            return self.almacen.aplicar_respuesta(
                respuesta, resultado, nuevo_estado="revision_manual", contexto=contexto, fase=fase,
            )
        pendientes.pop(0)
        if siguiente is not None:
            pendientes.insert(0, siguiente)
        if duda["tipo"] == "rango":
            claves = [f.model_dump() for f in filas]
            pendientes[:] = [p for p in pendientes if p["tipo"] != "rango" or p["fila"] in claves]
        estado, mensaje = self._preparar_turno(parte.nota, filas, contexto, catalogo, parte.version + 1)
        return self.almacen.aplicar_respuesta(
            respuesta, resultado, mensaje, nuevo_estado=estado, filas=filas, contexto=contexto, fase=fase,
        )

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
