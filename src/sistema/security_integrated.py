
"""Sistema integrado de seguridad PIA-3.

MQTT -> SQLite -> Worker -> STT -> Detector -> Outbox.

Incluye heartbeat periodico y detencion controlada.
Las alertas se publican solamente cuando
PIA3_ENABLE_OUTBOX=1.
"""

import logging
import os
import threading
from functools import partial
from pathlib import Path

from sistema.b1_event_store import AlmacenEventosB1
from sistema.b1_event_worker import WorkerEventosB1
from sistema.b1_evidence import EnriquecedorB1
from sistema.mqtt_client import (
    conectar_mqtt,
    publicar_estado,
    INTERVALO_HEARTBEAT,
)
from sistema.note_store import AlmacenNotas
from sistema.security_outbox import OutboxSeguridad
from sistema.persistent_ingress import crear_entrada_persistente
from sistema.persistent_worker import WorkerPersistente
from sistema.security_persistent_processor import (
    crear_procesador_persistente,
)
from sistema.outbox_worker import WorkerOutbox
from sistema.part_classification_store import AlmacenClasificacionB1
from sistema.part_classification_worker import WorkerClasificacionB1
from sistema.part_context import ClientePlanta
from sistema.part_memory import RepositorioMemoriaPostgres, WorkerMemoria
from sistema.part_memory_store import AlmacenMemoriaLocal
from sistema.part_store import AlmacenPartes
from sistema.part_worker import WorkerPartes
from sistema.postgres import ConfiguracionPostgres
from sistema.sensor_store import AlmacenSensoresB1
from sistema.sensor_worker import WorkerSensoresB1
from sistema.transcription_service import TranscripcionPrioritaria
from sistema.whisper_adapter import WhisperLocal


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def ejecutar_heartbeat(cliente, detener):
    """Publica el estado mientras el sistema este activo."""

    while not detener.is_set():
        # Esperar conexion sin bloquear indefinidamente.
        if not cliente.conectado.wait(timeout=1.0):
            continue

        if detener.is_set():
            break

        try:
            publicar_estado(cliente)
        except Exception:
            logger.exception("Error publicando heartbeat")

        # Permite detener el hilo inmediatamente.
        detener.wait(INTERVALO_HEARTBEAT)


def main():
    logger.info("Iniciando seguridad integrada PIA-3")

    ruta = Path(
        os.getenv(
            "PIA3_DB_PATH",
            "data_runtime/seguridad.sqlite3",
        )
    )

    publicar = (
        os.getenv("PIA3_ENABLE_OUTBOX", "0") == "1"
    )

    almacen = AlmacenNotas(ruta)
    outbox = OutboxSeguridad(ruta)
    outbox_borradores = OutboxSeguridad(ruta, borradores=True)
    outbox_b1 = OutboxSeguridad(ruta, b1=True)
    planta = ClientePlanta(os.getenv("PLANTA_API_URL", "http://localhost:8100"))
    partes = AlmacenPartes(almacen, outbox_borradores)
    worker_memoria = None
    try:
        config_memoria = ConfiguracionPostgres.desde_entorno()
    except ValueError as error:
        logger.warning("Sin sincronización de memoria PostgreSQL: %s", error)
    else:
        worker_memoria = WorkerMemoria(AlmacenMemoriaLocal(ruta), RepositorioMemoriaPostgres(config_memoria))

    logger.info("Base SQLite: %s", ruta)
    logger.info("Notas pendientes: %s", almacen.pendientes())
    logger.info("Notas agotadas: %s", almacen.agotadas())
    logger.info("Alertas pendientes: %s", outbox.pendientes())
    logger.info("Alertas agotadas: %s", outbox.agotadas())

    if publicar:
        logger.warning("Publicacion MQTT habilitada")
    else:
        logger.info("Modo seguro: publicaciones desactivadas")

    logger.info("Cargando motor Whisper")
    motor = TranscripcionPrioritaria(WhisperLocal(modelo="base"), almacen.hay_seguridad_lista)
    planta_partes = ClientePlanta(planta.url)
    worker_partes = WorkerPartes(
        partes, planta_partes.catalogo, historial=planta_partes.historial, fuentes_con_timeout=True,
        transcribir_respuesta=motor.transcribir_respuesta, hay_seguridad=almacen.hay_seguridad_lista,
        foto=planta_partes.foto_parte,
    )
    worker_b1 = WorkerClasificacionB1(
        AlmacenClasificacionB1(almacen.ruta), partial(planta.catalogo, requerir_sensores=True),
    )
    sensores = AlmacenSensoresB1(almacen.ruta, os.getenv("PIA3_SENSOR_EPOCH", "local"))
    worker_sensores = WorkerSensoresB1(sensores, planta)
    eventos_b1 = AlmacenEventosB1(outbox_b1)
    worker_eventos_b1 = WorkerEventosB1(eventos_b1, EnriquecedorB1(eventos_b1, planta))

    procesador = crear_procesador_persistente(
        almacen=almacen,
        motor=motor,
    )

    worker_notas = WorkerPersistente(
        almacen=almacen,
        procesar=procesador,
        intervalo=0.5,
        espera_error=2.0,
        finalizacion_transaccional=True,
    )

    entrada = crear_entrada_persistente(almacen)

    cliente = None
    worker_outbox = None
    worker_borradores = None
    worker_publicacion_b1 = None
    hilo_heartbeat = None

    detener_heartbeat = threading.Event()
    detener_sistema = threading.Event()

    mqtt_iniciado = False
    notas_iniciadas = False

    try:
        worker_notas.iniciar()
        notas_iniciadas = True

        cliente = conectar_mqtt(
            encolar_nota=entrada,
            encolar_respuesta=partes.guardar_respuesta,
            encolar_sensor=worker_sensores.encolar,
            discontinuidad_sensores=worker_sensores.marcar_discontinuidad,
        )

        cliente.loop_start()
        mqtt_iniciado = True

        # Reutilizar el heartbeat oficial ya implementado.
        hilo_heartbeat = threading.Thread(
            target=ejecutar_heartbeat,
            args=(cliente, detener_heartbeat),
            name="pia3-heartbeat",
            daemon=True,
        )
        hilo_heartbeat.start()

        if publicar:
            worker_outbox = WorkerOutbox(
                outbox=outbox,
                cliente=cliente,
                intervalo=0.5,
                espera_error=3.0,
            )
            worker_outbox.iniciar()
            worker_borradores = WorkerOutbox(outbox=outbox_borradores, cliente=cliente)
            worker_borradores.iniciar()

        worker_partes.iniciar()
        if worker_memoria is not None:
            worker_memoria.iniciar()
        worker_b1.iniciar()
        worker_sensores.iniciar()
        worker_eventos_b1.iniciar()
        if publicar:
            worker_publicacion_b1 = WorkerOutbox(outbox=outbox_b1, cliente=cliente)
            worker_publicacion_b1.iniciar()

        logger.info("Sistema integrado iniciado")

        while not detener_sistema.wait(0.5):
            pass

    except KeyboardInterrupt:
        logger.info("Detencion solicitada")

    except Exception:
        logger.exception("Fallo en sistema integrado")
        raise

    finally:
        detener_sistema.set()

        if not worker_partes.detener():
            logger.error("Worker de partes sigue activo")

        if worker_memoria is not None and not worker_memoria.detener():
            logger.error("Worker de memoria sigue activo")

        if not worker_b1.detener():
            logger.error("Worker de clasificación B1 sigue activo")

        if not worker_sensores.detener():
            logger.error("Worker de sensores B1 sigue activo")

        if not worker_eventos_b1.detener():
            logger.error("Worker de eventos B1 sigue activo")

        if worker_publicacion_b1 is not None and not worker_publicacion_b1.detener():
            logger.error("Publicador B1 sigue activo")

        if worker_borradores is not None and not worker_borradores.detener():
            logger.error("Publicador de borradores sigue activo")

        # Detener publicaciones de seguridad.
        if worker_outbox is not None:
            if not worker_outbox.detener(timeout=10):
                logger.error("Worker outbox sigue activo")

        # Detener heartbeat.
        detener_heartbeat.set()

        if hilo_heartbeat is not None:
            hilo_heartbeat.join(timeout=5)

        # Detener worker de notas.
        if notas_iniciadas:
            if not worker_notas.detener(timeout=30):
                logger.error("Worker de notas sigue activo")

        # Desconectar MQTT despues de detener workers.
        if cliente is not None:
            cliente.disconnect()

            if mqtt_iniciado:
                cliente.loop_stop()

        logger.info(
            "Notas pendientes: %s",
            almacen.pendientes(),
        )
        logger.info(
            "Notas agotadas: %s",
            almacen.agotadas(),
        )
        logger.info(
            "Alertas pendientes: %s",
            outbox.pendientes(),
        )
        logger.info(
            "Alertas agotadas: %s",
            outbox.agotadas(),
        )

        logger.info("Sistema integrado detenido")


if __name__ == "__main__":
    main()
