"""Etapa: agrupar las voces por persona, estén registradas o no.

    python -m reconocimiento.hablantes [carpeta_clase]   # identificacion.json + embeddings.npz → hablantes.json

Primero se juntan los celulares (fusion) y de cada intervención se usa el embedding del
micrófono que mejor la oyó. Luego se agrupan por parecido (clustering jerárquico con
distancia coseno). Cada grupo es una voz: si la mayoría de sus segmentos se reconocieron
como un alumno, lleva su nombre; si no, "voz N" (alguien sin registrar, p. ej. el docente).

También avisa cuando una misma voz fue reconocida como dos alumnos distintos: es la señal
de una posible suplantación ("presente" por otro) o de dos huellas demasiado parecidas.
Es una estimación: el número de voces depende de UMBRAL_MISMA_VOZ.
"""

from collections import Counter

import numpy as np

from .clase import carpeta_desde_args, escribir_json, leer_json
from .fusion import fusionar

# Elección propia: dos segmentos con similitud coseno ≥ 0.35 se consideran la misma voz.
# Entre personas distintas ECAPA suele dar < 0.2; la misma persona de lejos, 0.3-0.6.
UMBRAL_MISMA_VOZ = 0.35
# Elección propia: los segmentos de menos de 1 s dan embeddings ruidosos; no forman grupos,
# solo se suman a uno existente si se le parecen lo suficiente.
DURACION_GRUPO_S = 1.0


def agrupar(E: np.ndarray, duraciones: list[float]) -> list[int]:
    """Etiqueta de grupo por segmento (-1 = sin agrupar)."""
    from scipy.cluster.hierarchy import fcluster, linkage

    largos = [i for i, d in enumerate(duraciones) if d >= DURACION_GRUPO_S]
    etiquetas = [-1] * len(E)
    if len(largos) >= 2:
        Z = linkage(E[largos], method="average", metric="cosine")
        for i, g in zip(largos, fcluster(Z, t=1 - UMBRAL_MISMA_VOZ, criterion="distance")):
            etiquetas[i] = int(g)
    elif len(largos) == 1:
        etiquetas[largos[0]] = 1
    grupos = sorted({g for g in etiquetas if g > 0})
    if grupos:
        centros = np.array([E[[i for i, g in enumerate(etiquetas) if g == k]].mean(0) for k in grupos])
        centros /= np.linalg.norm(centros, axis=1, keepdims=True)
        for i, d in enumerate(duraciones):
            if etiquetas[i] == -1:
                sims = centros @ E[i]
                if sims.max() >= UMBRAL_MISMA_VOZ:
                    etiquetas[i] = grupos[int(np.argmax(sims))]
    return etiquetas


def main():
    carpeta = carpeta_desde_args()
    ident = leer_json(carpeta / "identificacion.json", "identificar")
    if not (carpeta / "embeddings.npz").exists():
        raise SystemExit("Falta embeddings.npz: vuelve a ejecutar python -m reconocimiento.identificar")
    E = np.load(carpeta / "embeddings.npz")["E"]
    segs = [{**s, "i": i} for i, s in enumerate(ident["segmentos"])]
    inter = fusionar(segs)
    Ei = E[[x["indice"] for x in inter]]
    etiquetas = agrupar(Ei, [x["fin_s"] - x["inicio_s"] for x in inter])

    voces = []
    numero = 0
    for g in sorted({e for e in etiquetas if e > 0}, key=lambda g: min(x["inicio_s"] for x, e in zip(inter, etiquetas) if e == g)):
        miembros = [x for x, e in zip(inter, etiquetas) if e == g]
        reconocidos = Counter(x["hablante"] for x in miembros if x["codigo"])
        # Lleva el nombre del alumno si al menos un tercio de sus intervenciones se le reconocieron.
        if reconocidos and reconocidos.most_common(1)[0][1] * 3 >= len(miembros):
            etiqueta = reconocidos.most_common(1)[0][0]
        else:
            numero += 1
            etiqueta = f"voz {numero} (sin registrar)"
        voces.append({
            "voz": etiqueta,
            "intervenciones": len(miembros),
            "voz_s": round(sum(x["fin_s"] - x["inicio_s"] for x in miembros), 1),
            "primera_s": miembros[0]["inicio_s"],
            "ultima_s": miembros[-1]["fin_s"],
            "reconocida_como": dict(reconocidos),
            "mics": dict(Counter(x["mic"] for x in miembros)),
            "alerta": (f"la misma voz se reconoció como {' y '.join(reconocidos)}" if len(reconocidos) > 1 else None),
            "momentos": [[x["inicio_s"], x["fin_s"]] for x in miembros],
        })
    voces.sort(key=lambda v: -v["voz_s"])
    sueltas = sum(1 for e in etiquetas if e <= 0)
    escribir_json(carpeta / "hablantes.json", {
        "umbral_misma_voz": UMBRAL_MISMA_VOZ, "intervenciones": len(inter),
        "sin_agrupar": sueltas, "voces": voces})
    print(f"  {len(inter)} intervenciones → {len(voces)} voces distintas ({sueltas} cortas sin agrupar)")
    for v in voces:
        print(f"    {v['voz']:38s} {v['intervenciones']:4d} interv. {v['voz_s']:7.1f} s  mics {v['mics']}"
              + (f"  ⚠ {v['alerta']}" if v["alerta"] else ""))
    print(f"→ {carpeta / 'hablantes.json'}")


if __name__ == "__main__":
    main()
