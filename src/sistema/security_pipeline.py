
"""Flujo local de análisis de notas de voz PIA-3."""

from dataclasses import dataclass

from sistema.audio_service import descargar_audio
from sistema.transcription_service import (
    MotorTranscripcion,
    transcribir_audio,
)
from sistema.security_detector import (
    CondicionInsegura,
    detectar_condiciones_inseguras,
)


@dataclass(frozen=True)
class ResultadoAnalisis:
    texto: str
    condiciones: list[CondicionInsegura]


def analizar_nota(
    audio_url: str,
    motor: MotorTranscripcion,
) -> ResultadoAnalisis:
    """Descarga, transcribe y analiza una nota."""

    audio = descargar_audio(audio_url)

    texto = transcribir_audio(audio, motor)

    condiciones = detectar_condiciones_inseguras(texto)

    return ResultadoAnalisis(
        texto=texto,
        condiciones=condiciones,
    )
