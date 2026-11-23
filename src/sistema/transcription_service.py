
"""Interfaz de transcripción local para PIA-3."""

import io
import wave
from typing import Protocol


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
