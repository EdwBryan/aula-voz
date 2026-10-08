"""Lectura de WAV y segmentación con Silero VAD."""

from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from .config import FS, SEGMENTO_MAX_S, SEGMENTO_MIN_S, SILERO
from .modelos import nuevo_vad

_vad = None


def leer(ruta: Path) -> np.ndarray:
    x, fs = sf.read(ruta, dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if fs != FS:
        import torchaudio.functional as F
        x = F.resample(torch.from_numpy(x), fs, FS).numpy()
    return x


def voz(x: np.ndarray) -> list[tuple[float, float]]:
    """Tramos con voz (inicio_s, fin_s) según Silero VAD con sus valores por defecto."""
    from silero_vad import get_speech_timestamps
    global _vad
    if _vad is None:
        _vad = nuevo_vad()
    ts = get_speech_timestamps(torch.from_numpy(x), _vad, sampling_rate=FS, return_seconds=False, **SILERO)
    return [(t["start"] / FS, t["end"] / FS) for t in ts]


def partir(a: float, b: float) -> list[tuple[float, float]]:
    """Corta un turno largo en trozos de hasta SEGMENTO_MAX_S (el último absorbe el resto corto)."""
    trozos, t = [], a
    while b - t > SEGMENTO_MAX_S:
        trozos.append((t, t + SEGMENTO_MAX_S))
        t += SEGMENTO_MAX_S
    if trozos and b - t < SEGMENTO_MIN_S:
        trozos[-1] = (trozos[-1][0], b)
    else:
        trozos.append((t, b))
    return trozos


def segmentos(x: np.ndarray) -> list[tuple[float, float]]:
    """Segmentos listos para identificar: voz, sin los muy cortos, cortados a SEGMENTO_MAX_S."""
    salida = []
    for a, b in voz(x):
        if b - a >= SEGMENTO_MIN_S:
            salida += partir(a, b)
    return salida


def recorte(x: np.ndarray, a: float, b: float) -> np.ndarray:
    return x[int(a * FS):int(b * FS)]
