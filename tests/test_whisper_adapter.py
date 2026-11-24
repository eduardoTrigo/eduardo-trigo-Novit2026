
"""Pruebas unitarias del adaptador Whisper local."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sistema.whisper_adapter import WhisperLocal


def test_transcribir_audio_con_modelo_simulado():
    with patch(
        "sistema.whisper_adapter.WhisperModel"
    ) as modelo_mock:

        modelo_mock.return_value.transcribe.return_value = (
            [
                SimpleNamespace(text=" Hay humo"),
                SimpleNamespace(text=" en la bomba "),
            ],
            None,
        )

        motor = WhisperLocal()

        texto = motor.transcribir(b"audio-de-prueba")

        assert texto == "Hay humo en la bomba"
        modelo_mock.return_value.transcribe.assert_called_once()


def test_modelo_se_carga_una_sola_vez():
    with patch(
        "sistema.whisper_adapter.WhisperModel"
    ) as modelo_mock:

        modelo_mock.return_value.transcribe.return_value = (
            [SimpleNamespace(text="Prueba")],
            None,
        )

        motor = WhisperLocal()

        motor.transcribir(b"audio-1")
        motor.transcribir(b"audio-2")

        modelo_mock.assert_called_once()
        assert modelo_mock.return_value.transcribe.call_count == 2


def test_tablet_descarta_generacion_alcanzada_por_plazo(monkeypatch):
    from sistema import whisper_adapter

    reloj = iter((100.0, 106.0, 106.0))
    monkeypatch.setattr(whisper_adapter, "monotonic", lambda: next(reloj))
    with patch("sistema.whisper_adapter.WhisperModel") as modelo:
        modelo.return_value.transcribe.return_value = ([SimpleNamespace(text="Confirmo.", tokens=[1])], None)
        motor = WhisperLocal()
        with pytest.raises(TimeoutError):
            motor.transcribir_respuesta(b"audio")
        assert modelo.return_value.transcribe.call_args.kwargs["max_new_tokens"] == 48
        modelo.assert_called_once()
