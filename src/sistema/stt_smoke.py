
"""Prueba real del pipeline de seguridad PIA-3.

Descarga un WAV, transcribe y detecta condiciones.
No publica alertas ni modifica la planta.
"""

import logging
import time

from sistema.whisper_adapter import WhisperLocal
from sistema.security_pipeline import analizar_nota


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def main():
    ruta_audio = "/media/audios/NOTA-00014.wav"

    print("\n=== PRUEBA REAL DE SEGURIDAD PIA-3 ===")
    print("Audio:", ruta_audio)

    inicio_carga = time.perf_counter()

    motor = WhisperLocal(modelo="base")

    tiempo_carga = time.perf_counter() - inicio_carga

    print("\nModelo STT cargado")
    print(f"Tiempo de carga: {tiempo_carga:.2f} s")

    inicio = time.perf_counter()

    resultado = analizar_nota(
        audio_url=ruta_audio,
        motor=motor,
    )

    tiempo_total = time.perf_counter() - inicio

    print("\n--- TRANSCRIPCIÓN ---")
    print(resultado.texto)

    print("\n--- SEGURIDAD ---")

    if not resultado.condiciones:
        print("Sin condiciones inseguras detectadas")
    else:
        for condicion in resultado.condiciones:
            print(
                f"ALERTA {condicion.codigo}: "
                f"{condicion.descripcion}"
            )

    print("\n--- RENDIMIENTO ---")
    print(f"Carga del modelo: {tiempo_carga:.2f} s")
    print(f"Pipeline completo: {tiempo_total:.2f} s")

    print("\nPrueba finalizada")


if __name__ == "__main__":
    main()
