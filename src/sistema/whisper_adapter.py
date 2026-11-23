
"""Adaptador experimental de STT local para PIA-3.

Motor: faster-whisper.
Autorización oficial de Novit: no confirmada.
"""

import io
import logging

from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


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
