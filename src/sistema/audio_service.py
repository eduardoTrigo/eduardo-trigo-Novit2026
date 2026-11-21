
"""Descarga de audios desde la API local de PIA-3."""

import os
from urllib.parse import urljoin

import httpx

MAX_AUDIO_BYTES = 20 * 1024 * 1024


def descargar_audio(audio_url: str) -> bytes:
    """Obtiene un audio WAV desde la API de la planta."""

    if (
        not audio_url.startswith("/media/audios/")
        or ".." in audio_url
        or "?" in audio_url
        or "#" in audio_url
    ):
        raise ValueError("Ruta de audio no permitida")

    api_url = os.getenv(
        "PLANTA_API_URL",
        "http://localhost:8100",
    )

    url = urljoin(api_url.rstrip("/") + "/", audio_url)

    with httpx.Client(timeout=10.0) as cliente:
        with cliente.stream("GET", url) as respuesta:
            respuesta.raise_for_status()

            contenido = bytearray()

            for fragmento in respuesta.iter_bytes():
                contenido.extend(fragmento)

                if len(contenido) > MAX_AUDIO_BYTES:
                    raise ValueError("Audio demasiado grande")

    if not contenido.startswith(b"RIFF"):
        raise ValueError("El archivo recibido no parece ser WAV")

    return bytes(contenido)
