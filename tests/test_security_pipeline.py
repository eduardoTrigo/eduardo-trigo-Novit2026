
"""Pruebas del flujo de análisis de notas."""

from unittest.mock import patch

from sistema.security_pipeline import analizar_nota
from test_transcription_service import crear_wav_prueba


class MotorSimulado:
    def transcribir(self, audio: bytes) -> str:
        return "Hay humo en la bomba y olor a gas"


def test_pipeline_detecta_dos_condiciones():
    audio = crear_wav_prueba()

    with patch(
        "sistema.security_pipeline.descargar_audio",
        return_value=audio,
    ) as descarga:

        resultado = analizar_nota(
            "/media/audios/NOTA-00001.wav",
            MotorSimulado(),
        )

    descarga.assert_called_once_with(
        "/media/audios/NOTA-00001.wav"
    )

    codigos = {
        condicion.codigo
        for condicion in resultado.condiciones
    }

    assert codigos == {"S2", "S3"}
