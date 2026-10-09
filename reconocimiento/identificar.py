"""Etapa: identificación de hablante con ECAPA.

    python -m reconocimiento.identificar [carpeta_clase]   # segmentos.json → identificacion.json y embeddings.npz

Cada segmento se compara con las huellas (data/huellas/). Si la similitud no pasa el
umbral queda como "desconocido".
"""

import time

import numpy as np

from . import audio
from .clase import carpeta_desde_args, desfases, escribir_json, leer_json, metadata
from .huellas import Huellas
from .modelos import embedding


def main():
    carpeta = carpeta_desde_args()
    meta = metadata(carpeta)
    segmentos = leer_json(carpeta / "segmentos.json", "vad")
    huellas = Huellas.cargar()
    desf = desfases(meta)
    filas, embs = [], []
    t0 = time.time()
    for mic, segs in segmentos.items():
        x = audio.leer(carpeta / meta["dispositivos"][mic]["archivo"])
        for a, b in segs:
            e = embedding(audio.recorte(x, a, b))
            r = huellas.identificar(e)
            embs.append(e)
            filas.append({"mic": mic, "inicio_s": a, "fin_s": b,
                          "inicio_clase_s": round(desf[mic] + a, 3), "fin_clase_s": round(desf[mic] + b, 3), **r})
    orden = sorted(range(len(filas)), key=lambda i: filas[i]["inicio_clase_s"])
    filas = [filas[i] for i in orden]
    # Embedding de cada segmento, en el mismo orden (para agrupar voces sin volver a calcular).
    np.savez(carpeta / "embeddings.npz", E=np.array([embs[i] for i in orden]).reshape(len(orden), -1))
    escribir_json(carpeta / "identificacion.json", {"umbral": huellas.umbral, "segmentos": filas})
    conocidos = sum(f["codigo"] is not None for f in filas)
    print(f"  {len(filas)} segmentos: {conocidos} reconocidos, {len(filas) - conocidos} desconocidos "
          f"(umbral {huellas.umbral}, {time.time() - t0:.1f} s)")
    print(f"→ {carpeta / 'identificacion.json'}")


if __name__ == "__main__":
    main()
