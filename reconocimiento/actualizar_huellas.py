"""Etapa: actualizar las huellas con lo que se oyó en una clase.

    python -m reconocimiento.actualizar_huellas [carpeta_clase]   # usa identificacion.json

Solo entran los segmentos muy seguros: similitud ≥ umbral + MARGEN_ACTUALIZAR y al menos
MARGEN_SEGUNDO por encima del segundo candidato. Así la huella aprende cómo suena cada
alumno de lejos, en el aula, sin arriesgarse a mezclar voces. La huella anterior se guarda
en data/huellas/historial/ y cada clase se aplica una sola vez.
"""

import json
import shutil
from datetime import datetime

import numpy as np

from . import audio
from .clase import carpeta_desde_args, leer_json, metadata
from .config import HUELLAS, MARGEN_ACTUALIZAR, MARGEN_SEGUNDO
from .huellas import Huellas, normalizar
from .modelos import embedding


def main():
    carpeta = carpeta_desde_args()
    meta = metadata(carpeta)
    aplicadas_f = HUELLAS / "aplicadas.json"
    aplicadas = json.loads(aplicadas_f.read_text()) if aplicadas_f.exists() else []
    if carpeta.name in aplicadas:
        print(f"La clase {carpeta.name} ya se usó para actualizar las huellas.")
        return

    h = Huellas.cargar()
    ident = leer_json(carpeta / "identificacion.json", "identificar")
    minimo = h.umbral + MARGEN_ACTUALIZAR
    seguros = [s for s in ident["segmentos"]
               if s["codigo"] and s["similitud"] >= minimo and s["similitud"] - s["segundo"] >= MARGEN_SEGUNDO]
    if not seguros:
        print(f"Ningún segmento con similitud ≥ {minimo:.2f}: las huellas no cambian.")
        return

    wavs = {}
    nuevos: dict[str, list] = {}
    for s in seguros:
        if s["mic"] not in wavs:
            wavs[s["mic"]] = audio.leer(carpeta / meta["dispositivos"][s["mic"]]["archivo"])
        nuevos.setdefault(s["codigo"], []).append(embedding(audio.recorte(wavs[s["mic"]], s["inicio_s"], s["fin_s"])))

    historial = HUELLAS / "historial" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_antes_de_{carpeta.name}"
    historial.mkdir(parents=True)
    for f in ("huellas.npz", "alumnos.json"):
        shutil.copy2(HUELLAS / f, historial / f)

    for codigo, embs in nuevos.items():
        i = h.codigos.index(codigo)
        antes = h.matriz[i].copy()
        h.matriz[i] = normalizar(antes * h.cuenta[i] + np.sum(embs, axis=0))
        h.cuenta[i] += len(embs)
        print(f"  {h.nombres[i]:40s} +{len(embs)} segmentos (total {h.cuenta[i]}), "
              f"la huella se movió {1 - float(antes @ h.matriz[i]):.3f}")
    h.guardar()
    aplicadas_f.write_text(json.dumps(aplicadas + [carpeta.name], indent=1))
    print(f"→ {HUELLAS}  (anterior en {historial.relative_to(HUELLAS.parent.parent)})")


if __name__ == "__main__":
    main()
