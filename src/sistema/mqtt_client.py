"""Cliente MQTT para la comunicación con la planta PIA-3."""

import logging
import os
import threading
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from collections.abc import Callable

from planta_contrato import EstadoSistema, NotaRecorrida, RespuestaOperador, Topicos
from pydantic import ValidationError
from sistema.nota_router import recibir_nota_mqtt

logger = logging.getLogger(__name__)

INTERVALO_HEARTBEAT = 30
PREFIJO_SENSORES = Topicos.SENSORES.split("{", 1)[0]


def conectar_mqtt(
    encolar_nota: Callable[[NotaRecorrida], bool] | None = None,
    *,
    encolar_respuesta: Callable[[RespuestaOperador], bool] | None = None,
    encolar_sensor: Callable[[str, bytes], bool] | None = None,
    discontinuidad_sensores: Callable[[], None] | None = None,
) -> mqtt.Client:
    """Configura y conecta el cliente MQTT al broker de PIA-3."""

    host = os.getenv("MQTT_HOST", "localhost")
    puerto = int(os.getenv("MQTT_PORT", "1883"))

    cliente = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )

    # Señal que indica si el broker confirmó la conexión.
    cliente.conectado = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        """Se ejecuta cuando el broker responde a la conexión."""

        if reason_code == 0:
            logger.info(
                "Conectado al broker MQTT: %s:%s",
                host,
                puerto,
            )

            # Suscribirse nuevamente después de una reconexión.
            resultado, _ = client.subscribe(Topicos.RECORRIDAS)

            if resultado == mqtt.MQTT_ERR_SUCCESS:
                logger.info(
                    "Suscripto a %s",
                    Topicos.RECORRIDAS,
                )
            else:
                logger.error(
                    "Error al suscribirse: %s",
                    resultado,
                )

            if encolar_respuesta is not None:
                resultado_respuestas, _ = client.subscribe(Topicos.RESPUESTAS, qos=1)
                if resultado_respuestas != mqtt.MQTT_ERR_SUCCESS:
                    logger.error("Error al suscribirse a %s: %s", Topicos.RESPUESTAS, resultado_respuestas)

            if encolar_sensor is not None:
                for topico, qos in ((f"{PREFIJO_SENSORES}#", 0), (Topicos.ENERGIA, 1)):
                    resultado, _ = client.subscribe(topico, qos=qos)
                    if resultado != mqtt.MQTT_ERR_SUCCESS:
                        logger.error("Error al suscribirse a %s: %s", topico, resultado)

            client.conectado.set()

        else:
            client.conectado.clear()
            logger.error(
                "Conexión MQTT rechazada: %s",
                reason_code,
            )

    def on_disconnect(
        client, userdata, disconnect_flags, reason_code, properties
    ):
        """Registra la pérdida de conexión con el broker."""

        client.conectado.clear()
        if discontinuidad_sensores is not None:
            discontinuidad_sensores()

        logger.warning(
            "MQTT desconectado: %s",
            reason_code,
        )

    
    def on_message(client, userdata, mensaje):
        """Recibe los mensajes MQTT de la planta."""

        if encolar_sensor is not None and (
            mensaje.topic.startswith(PREFIJO_SENSORES) or mensaje.topic == Topicos.ENERGIA
        ):
            encolar_sensor(mensaje.topic, mensaje.payload)
            return

        if mensaje.topic == Topicos.RESPUESTAS and encolar_respuesta is not None:
            try:
                respuesta = RespuestaOperador.model_validate_json(mensaje.payload, strict=True)
            except ValidationError:
                logger.warning("Mensaje MQTT inválido en %s", Topicos.RESPUESTAS)
                return
            try:
                encolar_respuesta(respuesta)
            except Exception:
                logger.exception("Error al persistir la respuesta %s", respuesta.id)
            return

        if encolar_nota is not None:
            recibir_nota_mqtt(
                topico=mensaje.topic,
                payload=mensaje.payload,
                encolar=encolar_nota,
            )
            return

        # Comportamiento anterior, cuando no hay worker.
        contenido = mensaje.payload.decode(
            "utf-8",
            errors="replace",
        )

        logger.info(
            "Mensaje recibido | Tópico: %s | Contenido: %s",
            mensaje.topic,
            contenido,
        )


    cliente.on_connect = on_connect
    cliente.on_disconnect = on_disconnect
    cliente.on_message = on_message

    # Configuración de reconexiones automáticas.
    cliente.reconnect_delay_set(
        min_delay=1,
        max_delay=30,
    )

    cliente.connect(host, puerto, keepalive=60)

    return cliente


def publicar_estado(cliente: mqtt.Client) -> None:
    """Publica el latido utilizando el contrato oficial de Novit."""

    if not cliente.conectado.is_set():
        logger.warning(
            "Heartbeat omitido: MQTT no está conectado"
        )
        return

    estado = EstadoSistema(
        ts=datetime.now(timezone.utc),
        sistema=os.getenv("SISTEMA_NOMBRE", "Eduardo-Trigo"),
        enlace_ok=True,  # Provisional: implementar detección real.
        pendientes_sincronizar=0,  # Provisional: implementar cola.
    )

    resultado = cliente.publish(
        topic=Topicos.ESTADO,
        payload=estado.model_dump_json(),
        qos=1,
    )

    if resultado.rc == mqtt.MQTT_ERR_SUCCESS:
        logger.info(
            "Heartbeat solicitado en %s",
            Topicos.ESTADO,
        )
    else:
        logger.error(
            "Error al publicar heartbeat: %s",
            resultado.rc,
        )


def iniciar_heartbeat(cliente: mqtt.Client) -> None:
    """Inicia un hilo que publica un latido cada 30 segundos."""

    def tarea():
        while True:

            # Bloquea el hilo hasta confirmar conexión.
            cliente.conectado.wait()

            publicar_estado(cliente)

            # Espera antes del siguiente latido.
            threading.Event().wait(INTERVALO_HEARTBEAT)

    hilo = threading.Thread(
        target=tarea,
        daemon=True,
        name="pia3-heartbeat",
    )

    hilo.start()
