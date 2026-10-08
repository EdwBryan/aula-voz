"""Etapa: actualizar las huellas con lo que se oyó en una clase.

    python -m reconocimiento.actualizar_huellas [carpeta_clase]   # usa identificacion.json
    python -m reconocimiento.actualizar_huellas [carpeta_clase] --confirmar CODIGO

Solo entran los segmentos muy seguros: similitud ≥ umbral + MARGEN_ACTUALIZAR y al menos
MARGEN_SEGUNDO por encima del segundo candidato. Con --confirmar (una persona confirmó que
esa voz era del alumno) entran sus segmentos con similitud ≥ MINIMO_CONFIRMADO aunque no
pasen el umbral, siempre que sea el candidato más parecido. Así la huella aprende cómo suena cada
alumno de lejos, en el aula, sin arriesgarse a mezclar voces. La huella anterior se guarda
en data/huellas/historial/ y cada clase se aplica una sola vez.
"""

import argparse
import json
import shutil
from datetime import datetime

from pathlib import Path

import numpy as np

from . import audio
from .clase import leer_json, metadata
from .config import CLASES, HUELLAS, MARGEN_ACTUALIZAR, MARGEN_SEGUNDO
from .huellas import Huellas, normalizar
from .modelos import embedding

# Elección propia: con una confirmación humana se acepta voz lejana, pero no lo que casi no
# se parece (por debajo de 0.20 suele ser ruido u otra persona).
MINIMO_CONFIRMADO = 0.20


def main():
    args = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    args.add_argument("carpeta", nargs="?", help="carpeta de la clase (por defecto la más reciente)")
    args.add_argument("--confirmar", metavar="CODIGO", help="código del alumno que confirmó que esas voces eran suyas")
    a = args.parse_args()
    if a.carpeta:
        carpeta = Path(a.carpeta) if Path(a.carpeta).is_dir() else CLASES / a.carpeta
    else:
        carpeta = sorted(p for p in CLASES.iterdir() if (p / "metadata.json").exists())[-1]
    meta = metadata(carpeta)
    aplicadas_f = HUELLAS / "aplicadas.json"
    aplicadas = json.loads(aplicadas_f.read_text()) if aplicadas_f.exists() else []
    if carpeta.name in aplicadas:
        print(f"La clase {carpeta.name} ya se usó para actualizar las huellas.")
        return

    h = Huellas.cargar()
    ident = leer_json(carpeta / "identificacion.json", "identificar")
    minimo = h.umbral + MARGEN_ACTUALIZAR
    if a.confirmar:
        if a.confirmar not in h.codigos:
            raise SystemExit(f"No hay huella con el código {a.confirmar}.")
        seguros = [s for s in ident["segmentos"]
                   if s["candidato"] == a.confirmar and s["similitud"] >= MINIMO_CONFIRMADO]
        criterio = f"confirmado {a.confirmar}, similitud ≥ {MINIMO_CONFIRMADO}"
    else:
        seguros = [s for s in ident["segmentos"]
                   if s["codigo"] and s["similitud"] >= minimo and s["similitud"] - s["segundo"] >= MARGEN_SEGUNDO]
        criterio = f"similitud ≥ {minimo:.2f}"
    if not seguros:
        print(f"Ningún segmento cumple ({criterio}): las huellas no cambian.")
        return
    print(f"Criterio: {criterio}")

    wavs = {}
    nuevos: dict[str, list] = {}
    for s in seguros:
        if s["mic"] not in wavs:
            wavs[s["mic"]] = audio.leer(carpeta / meta["dispositivos"][s["mic"]]["archivo"])
        nuevos.setdefault(a.confirmar or s["codigo"], []).append(embedding(audio.recorte(wavs[s["mic"]], s["inicio_s"], s["fin_s"])))

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
