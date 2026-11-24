
"""Adaptador experimental de STT local para PIA-3.

Motor: faster-whisper.
Autorización oficial de Novit: no confirmada.
"""

import io
import logging
from time import monotonic

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)
_PLAZO_RESPUESTA = 5.0
_TOKENS_RESPUESTA = 48


class WhisperLocal:
    """Motor de transcripción ejecutado en CPU."""

    def __init__(self, modelo: str = "base"):
        logger.info("Cargando modelo STT: %s", modelo)

        self.modelo = WhisperModel(
            modelo,
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            num_workers=1,
        )

        logger.info("Modelo STT cargado")

    def transcribir(self, audio: bytes) -> str:
        """Transcribe audio WAV recibido en memoria."""

        archivo = io.BytesIO(audio)

        segmentos, _ = self.modelo.transcribe(
            archivo,
            language="es",
            beam_size=1,
            vad_filter=False,
        )

        texto = " ".join(
            segmento.text.strip()
            for segmento in segmentos
        ).strip()

        logger.info(
            "Transcripción completada: %s",
            texto,
        )

        return texto

    def transcribir_respuesta(self, audio: bytes) -> str:
        """Transcribe un WAV corto de tablet con generación acotada."""
        vence = monotonic() + _PLAZO_RESPUESTA
        segmentos, _ = self.modelo.transcribe(
            io.BytesIO(audio), language="es", beam_size=1, vad_filter=False,
            temperature=0.0, condition_on_previous_text=False, max_new_tokens=_TOKENS_RESPUESTA,
        )
        texto = []
        tokens = 0
        for segmento in segmentos:
            if monotonic() >= vence:
                break
            tokens += len(segmento.tokens)
            if tokens >= _TOKENS_RESPUESTA:
                raise ValueError("La transcripción de tablet alcanzó el límite de generación")
            texto.append(segmento.text.strip())
        if monotonic() >= vence:
            raise TimeoutError("Se agotó el plazo de transcripción de tablet")
        return " ".join(texto).strip()
