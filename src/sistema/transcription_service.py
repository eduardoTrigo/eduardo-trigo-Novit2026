
"""Interfaz de transcripción local para PIA-3."""

import io
import wave
from collections.abc import Callable
from threading import Lock
from typing import Protocol


MAX_SEGUNDOS_RESPUESTA = 5
MAX_BYTES_RESPUESTA = MAX_SEGUNDOS_RESPUESTA * 16000 * 2 + 4096


class MotorTranscripcion(Protocol):
    """Contrato que debe cumplir el motor STT local."""

    def transcribir(self, audio: bytes) -> str:
        """Convierte audio WAV en texto."""
        ...


def validar_audio(audio: bytes) -> float:
    """Valida formato y devuelve duración en segundos."""

    try:
        with wave.open(io.BytesIO(audio), "rb") as wav:
            canales = wav.getnchannels()
            frecuencia = wav.getframerate()
            bits = wav.getsampwidth() * 8

            if canales != 1 or frecuencia != 16000 or bits != 16:
                raise ValueError(
                    "Se requiere WAV mono, 16 kHz, 16 bits"
                )

            return wav.getnframes() / frecuencia

    except (wave.Error, EOFError) as error:
        raise ValueError("Archivo WAV inválido") from error


def transcribir_audio(
    audio: bytes,
    motor: MotorTranscripcion,
) -> str:
    """Valida el WAV y lo envía al motor local."""

    validar_audio(audio)
    texto = motor.transcribir(audio).strip()

    if not texto:
        raise ValueError("La transcripción está vacía")

    return texto


class TranscripcionPrioritaria:
    """Comparte el motor local con tablet cuando el worker de seguridad está libre."""

    def __init__(self, motor, hay_seguridad: Callable[[], bool]):
        self._motor = motor
        self._hay_seguridad = hay_seguridad
        self._motor_en_uso = Lock()

    def transcribir(self, audio: bytes) -> str:
        with self._motor_en_uso:
            return self._motor.transcribir(audio)

    def transcribir_respuesta(self, audio: bytes) -> str | None:
        """Devuelve None si corresponde aplazar el turno por prioridad de seguridad."""
        if not 0 < validar_audio(audio) <= MAX_SEGUNDOS_RESPUESTA:
            raise ValueError("La respuesta de voz excede la duración admitida o está vacía")
        if not self._motor_en_uso.acquire(blocking=False):
            return None
        try:
            if self._hay_seguridad():
                return None
            return self._motor.transcribir_respuesta(audio)
        finally:
            self._motor_en_uso.release()
