
import io
import wave

import pytest

from sistema.transcription_service import (
    transcribir_audio,
    validar_audio,
)


def crear_wav_prueba():
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)

    return buffer.getvalue()


class MotorSimulado:
    def transcribir(self, audio: bytes) -> str:
        return "Hay humo en la bomba"


def test_validar_wav():
    duracion = validar_audio(crear_wav_prueba())

    assert duracion == pytest.approx(1.0)


def test_transcripcion_simulada():
    texto = transcribir_audio(
        crear_wav_prueba(),
        MotorSimulado(),
    )

    assert texto == "Hay humo en la bomba"


def test_rechazar_audio_invalido():
    with pytest.raises(ValueError):
        validar_audio(b"audio incorrecto")

