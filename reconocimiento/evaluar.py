"""Evaluación con audios anotados (métricas para el informe).

    python -m reconocimiento.evaluar [referencia.json]    # por defecto data/evaluacion/referencia.json

La referencia (local, no se sube) dice qué audio usar como registro de cada persona y,
en cada prueba, los tramos (inicio, fin, persona). Se mide:
  A) identificación sola: ECAPA sobre los tramos anotados (sin depender del VAD);
  B) extremo a extremo: Silero VAD + ECAPA, como en clase.
Las huellas de la evaluación son aparte: no tocan data/huellas/.
"""

import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audio
from .config import DATOS, METRICAS, RAIZ, SEGMENTO_MIN_S
from .huellas import calibrar, construir
from .modelos import embedding

DESC = "desconocido"


def solape(p, q) -> float:
    return max(0.0, min(p[1], q[1]) - max(p[0], q[0]))


def medir(predichos: list[str], verdad: list[str], personas: list[str]) -> dict:
    etiquetas = personas + [DESC, "perdido"]
    conf = {v: {p: 0 for p in etiquetas} for v in personas}
    for p, v in zip(predichos, verdad):
        conf[v][p] += 1
    aciertos = sum(p == v for p, v in zip(predichos, verdad))
    return {"total": len(verdad), "aciertos": aciertos, "acierto_pct": round(100 * aciertos / max(len(verdad), 1), 1),
            "fallos": dict(Counter(p for p, v in zip(predichos, verdad) if p != v)), "confusion": conf}


def main():
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else DATOS / "evaluacion" / "referencia.json"
    ref = json.loads(ruta.read_text(encoding="utf-8"))

    # Registro: cada tramo con voz del audio de registro es una toma.
    alumnos = {}
    for persona, archivo in ref["registro"].items():
        x = audio.leer(RAIZ / archivo)
        tramos = [(a, b) for a, b in audio.voz(x)]
        alumnos[persona] = {"nombre": persona, "embeddings": np.array([embedding(audio.recorte(x, a, b)) for a, b in tramos])}
    cal = calibrar(alumnos)
    huellas = construir(alumnos, cal["umbral"])
    personas = huellas.codigos
    print(f"Registro: {', '.join(f'{p} {len(alumnos[p]['embeddings'])} tomas' for p in personas)}")
    print(f"Umbral calibrado: {cal['umbral']} ({cal['metodo']}); propias mín {cal['propias_min']}, ajenas máx {cal['ajenas_max']}\n")

    resultados = {}
    for nombre, prueba in ref["pruebas"].items():
        x = audio.leer(RAIZ / prueba["archivo"])
        anot = prueba["anotacion"]
        verdad = [p for _, _, p in anot]
        hasta = prueba.get("confiable_hasta")

        # A) identificación sobre los tramos anotados
        t = time.time()
        ids_a = [huellas.identificar(embedding(audio.recorte(x, a, b))) for a, b, _ in anot]
        ms_seg = 1000 * (time.time() - t) / len(anot)
        pred_a = [r["nombre"] if r["codigo"] else DESC for r in ids_a]

        # B) extremo a extremo: VAD → segmentos (sin mínimo, para ver qué pasa con los cortos)
        voz = audio.voz(x)
        segs = [s for a, b in voz for s in audio.partir(a, b)]
        ids_b = [huellas.identificar(embedding(audio.recorte(x, a, b))) for a, b in segs]
        pred_b, pred_b_min, cortos = [], [], 0
        for a, b, _ in anot:
            s = [solape((a, b), q) for q in segs]
            k = int(np.argmax(s)) if s else -1
            if k < 0 or s[k] == 0:
                pred_b.append("perdido")
                pred_b_min.append("perdido")
                continue
            r = ids_b[k]
            pred_b.append(r["nombre"] if r["codigo"] else DESC)
            dur = segs[k][1] - segs[k][0]
            cortos += dur < SEGMENTO_MIN_S
            pred_b_min.append("perdido" if dur < SEGMENTO_MIN_S else pred_b[-1])
        usados = Counter(int(np.argmax([solape((a, b), q) for q in segs])) for a, b, _ in anot)

        res = {
            "anotados": len(anot),
            "A_identificacion": medir(pred_a, verdad, personas),
            "B_extremo_a_extremo": medir(pred_b, verdad, personas),
            f"B_con_minimo_{SEGMENTO_MIN_S}s": medir(pred_b_min, verdad, personas),
            "segmentos_vad": len(segs),
            "segmentos_con_dos_presentes": sum(1 for n in usados.values() if n > 1),
            "presentes_en_segmentos_cortos": cortos,
            "duracion_presente_s": {"mediana": round(float(np.median([b - a for a, b, _ in anot])), 2),
                                    "min": round(min(b - a for a, b, _ in anot), 2)},
            "ms_por_segmento_ecapa": round(ms_seg, 1),
            "acierto_sin_umbral": sum(r["candidato"] == v for r, v in zip(ids_a, verdad)),
            "similitudes": [r["similitud"] for r in ids_a],
        }
        if hasta:
            res[f"A_primeros_{hasta}"] = medir(pred_a[:hasta], verdad[:hasta], personas)
            res[f"B_primeros_{hasta}"] = medir(pred_b[:hasta], verdad[:hasta], personas)
        resultados[nombre] = res

        print(f"{nombre}: {len(anot)} presentes anotados, mediana {res['duracion_presente_s']['mediana']} s")
        for clave in [k for k in res if k[:2] in ("A_", "B_")]:
            m = res[clave]
            print(f"  {clave:28s} {m['aciertos']:3d}/{m['total']:<3d} = {m['acierto_pct']:5.1f} %   fallos: {m['fallos'] or '-'}")
        print(f"  Sin umbral (gana el más parecido): {res['acierto_sin_umbral']}/{len(anot)}")
        print(f"  VAD: {len(segs)} segmentos, {res['segmentos_con_dos_presentes']} con dos presentes pegados, "
              f"{cortos} presentes en segmentos < {SEGMENTO_MIN_S} s;  ECAPA {ms_seg:.0f} ms/segmento\n")

    METRICAS.mkdir(parents=True, exist_ok=True)
    archivo = METRICAS / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_evaluacion.json"
    archivo.write_text(json.dumps({"referencia": str(ruta), "calibracion": cal, "pruebas": resultados},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {archivo.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
