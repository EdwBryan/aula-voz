"""Etapa: detección de voz con Silero VAD.

    python -m reconocimiento.vad [carpeta_clase]     # → segmentos.json

Por cada micrófono, los segmentos con voz (en segundos dentro de su WAV), sin los
de menos de 0.5 s y con los turnos largos cortados en trozos de 6 s.
"""

import time

from . import audio
from .clase import carpeta_desde_args, escribir_json, metadata


def main():
    carpeta = carpeta_desde_args()
    meta = metadata(carpeta)
    salida = {}
    for mic, d in meta["dispositivos"].items():
        t = time.time()
        x = audio.leer(carpeta / d["archivo"])
        segs = audio.segmentos(x)
        salida[mic] = [[round(a, 3), round(b, 3)] for a, b in segs]
        voz = sum(b - a for a, b in segs)
        print(f"  {mic}: {len(segs)} segmentos, {voz:.0f} s de voz en {len(x) / audio.FS:.0f} s "
              f"(procesado en {time.time() - t:.1f} s)")
    escribir_json(carpeta / "segmentos.json", salida)
    print(f"→ {carpeta / 'segmentos.json'}")


if __name__ == "__main__":
    main()
