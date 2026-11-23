
"""Pruebas del servicio de descarga de audio PIA-3."""

import httpx
import pytest

from sistema import audio_service


# Audio ficticio para probar la descarga HTTP.
WAV_PRUEBA = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 8


def preparar_cliente_http(monkeypatch, respuesta):
    """Reemplaza la conexión real por una respuesta simulada."""

    cliente_original = httpx.Client

    transporte = httpx.MockTransport(
        lambda request: respuesta
    )

    monkeypatch.setattr(
        audio_service.httpx,
        "Client",
        lambda **kwargs: cliente_original(
            transport=transporte,
            **kwargs,
        ),
    )


def test_descargar_audio_correctamente(monkeypatch):
    """Debe descargar los bytes del audio solicitado."""

    monkeypatch.setenv(
        "PLANTA_API_URL",
        "http://localhost:8100",
    )

    preparar_cliente_http(
        monkeypatch,
        httpx.Response(200, content=WAV_PRUEBA),
    )

    audio = audio_service.descargar_audio(
        "/media/audios/NOTA-00001.wav"
    )

    assert audio == WAV_PRUEBA


@pytest.mark.parametrize(
    "ruta",
    [
        "https://ejemplo.com/audio.wav",
        "/api/verdad",
        "/media/audios/../secreto.txt",
        "/media/audios/nota.wav?otra=ruta",
    ],
)
def test_rechazar_rutas_no_permitidas(ruta):
    """No debe descargar recursos ajenos al audio."""

    with pytest.raises(ValueError):
        audio_service.descargar_audio(ruta)


def test_rechazar_archivo_que_no_es_wav(monkeypatch):
    """Debe rechazar respuestas que no parecen WAV."""

    preparar_cliente_http(
        monkeypatch,
        httpx.Response(200, content=b"archivo incorrecto"),
    )

    with pytest.raises(ValueError):
        audio_service.descargar_audio(
            "/media/audios/NOTA-00001.wav"
        )


def test_error_http(monkeypatch):
    """Debe informar si la planta responde con error."""

    preparar_cliente_http(
        monkeypatch,
        httpx.Response(404, content=b"No encontrado"),
    )

    with pytest.raises(httpx.HTTPStatusError):
        audio_service.descargar_audio(
            "/media/audios/NOTA-00001.wav"
        )
