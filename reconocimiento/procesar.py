"""Todas las etapas seguidas sobre una clase guardada.

    python -m reconocimiento.procesar [carpeta_clase]
"""

import sys

from . import asistencia, fusion, hablantes, identificar, vad


def main():
    for etapa in (vad, identificar, fusion, hablantes, asistencia):
        print(f"[{etapa.__name__.split('.')[-1]}]")
        etapa.main()


if __name__ == "__main__":
    sys.exit(main())
