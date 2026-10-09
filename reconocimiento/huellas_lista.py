"""Etapa: crear o reforzar huellas con las respuestas de la lista (ya revisadas).

    python -m reconocimiento.huellas_lista [carpeta_clase]

Lee lista.csv de la clase (se puede corregir a mano: columnas codigo y alumno) y
lista_embeddings.npz. Por cada respuesta con código y sin alerta de misma voz:
- si el alumno ya tiene huella, se le suma (como actualizar_huellas);
- si no, se crea su huella con esa respuesta (origen "lista": voz del aula, sin registro).
Solo para alumnos que aceptaron que se use su voz. La huella anterior queda en historial/.
"""

import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from .config import CLASES, HUELLAS
from .huellas import Huellas, normalizar


def main():
    carpeta = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(p for p in CLASES.iterdir() if (p / "lista.csv").exists())[-1]
    if not carpeta.is_dir():
        carpeta = CLASES / sys.argv[1]
    with open(carpeta / "lista.csv", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    arr = np.load(carpeta / "lista_embeddings.npz")
    emb = {int(n): e for n, e in zip(arr["n"], arr["E"])}

    usar = [f for f in filas if f["estado"] == "respuesta" and f.get("codigo") and not f.get("alerta") and int(f["n"]) in emb]
    if not usar:
        raise SystemExit("Ninguna respuesta con código y sin alerta en lista.csv (completa la columna codigo).")

    h = Huellas.cargar()
    historial = HUELLAS / "historial" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_antes_de_lista_{carpeta.name}"
    historial.mkdir(parents=True)
    for nombre in ("huellas.npz", "alumnos.json"):
        shutil.copy2(HUELLAS / nombre, historial / nombre)

    origen_f = HUELLAS / "origen.json"
    origen = json.loads(origen_f.read_text()) if origen_f.exists() else {}
    for f in usar:
        c, e = f["codigo"].strip(), emb[int(f["n"])]
        if c in h.codigos:
            i = h.codigos.index(c)
            h.matriz[i] = normalizar(h.matriz[i] * h.cuenta[i] + e)
            h.cuenta[i] += 1
            print(f"  {h.nombres[i]:40s} reforzada (total {h.cuenta[i]})")
        else:
            h.codigos.append(c)
            h.nombres.append(f.get("alumno") or f["llamada"])
            h.matriz = np.vstack([h.matriz, e])
            h.cuenta = np.append(h.cuenta, 1)
            origen[c] = {"origen": "lista", "clase": carpeta.name, "llamada": f["llamada"]}
            print(f"  {h.nombres[-1]:40s} nueva (desde la lista)")
    h.guardar()
    origen_f.write_text(json.dumps(origen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {len(h.codigos)} huellas en {HUELLAS} (anterior en historial/)")


if __name__ == "__main__":
    main()
