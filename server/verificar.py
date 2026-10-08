"""Comprueba con soundfile que todos los WAV guardados sean 16 kHz, mono, PCM 16 bits.

Uso:  python -m server.verificar [carpeta]      (por defecto: data/)
"""

import sys
from pathlib import Path

from .audio import verificar_wav


def main() -> int:
    raiz = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data"
    archivos = sorted(raiz.rglob("*.wav"))
    if not archivos:
        print(f"No hay archivos .wav en {raiz}")
        return 1
    malos = 0
    for ruta in archivos:
        v = verificar_wav(ruta)
        malos += not v["ok"]
        print(f"{'OK ' if v['ok'] else 'MAL'}  {v['frecuencia_hz']} Hz  {v['canales']} canal  "
              f"{v['formato']}/{v['subtipo']}  {v['duracion_s']:6.2f} s  {ruta.relative_to(raiz)}")
    print(f"\n{len(archivos) - malos}/{len(archivos)} archivos correctos (16000 Hz, mono, PCM_16).")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())
