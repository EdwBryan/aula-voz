"""Carga perezosa de Silero VAD y ECAPA (una sola vez por proceso)."""

import logging
import warnings
from functools import lru_cache

import numpy as np
import torch

from .config import FS, MODELO_ECAPA, MODELOS, SILERO

warnings.filterwarnings("ignore", module="speechbrain")
warnings.filterwarnings("ignore", category=UserWarning, module="torch")
logging.getLogger("speechbrain").setLevel(logging.WARNING)
torch.set_num_threads(4)

CARPETA_ECAPA = MODELOS / "ecapa"


def nuevo_vad():
    """Un modelo Silero nuevo. Guarda estado interno, así que en vivo va uno por micrófono."""
    from silero_vad import load_silero_vad
    return load_silero_vad()


@lru_cache(maxsize=1)
def ecapa():
    """ECAPA desde modelos/ecapa/. La primera vez lo descarga (necesita internet)."""
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    origen = str(CARPETA_ECAPA) if (CARPETA_ECAPA / "hyperparams.yaml").exists() else MODELO_ECAPA
    return EncoderClassifier.from_hparams(source=origen, savedir=str(CARPETA_ECAPA),
                                          run_opts={"device": "cpu"}, local_strategy=LocalStrategy.COPY)


def embedding(x: np.ndarray) -> np.ndarray:
    """Vector de 192 números que resume la voz, con norma 1. x: float32 a 16 kHz."""
    with torch.no_grad():
        v = ecapa().encode_batch(torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)).unsqueeze(0))
    v = v.squeeze().cpu().numpy()
    return v / np.linalg.norm(v)


def descargar():
    """Deja todo listo para trabajar sin internet."""
    nuevo_vad()
    ecapa()
    from faster_whisper import WhisperModel
    WhisperModel("small", device="cpu", compute_type="int8", download_root=str(MODELOS / "whisper"))
    print(f"Modelos listos en {MODELOS}")


if __name__ == "__main__":
    descargar()
