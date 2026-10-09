"""Etapa: juntar lo que oyeron varios celulares a la vez.

    python -m reconocimiento.fusion [carpeta_clase]   # identificacion.json → intervenciones.json y .csv

Una misma voz llega a 2 o 3 celulares. Los segmentos de distintos micrófonos que se solapan
en el tiempo de la clase se juntan en una sola intervención y se queda la identificación del
micrófono que mejor la oyó (mayor similitud). La salida sigue el contrato del grupo de
predicción: clase_id, curso, tema, inicio_s, fin_s, hablante, texto (texto vacío hasta
que exista la transcripción).
"""

import csv

from .asistencia import datos_clase
from .clase import carpeta_desde_args, escribir_json, leer_json, metadata

# Elección propia: los relojes de los celulares pueden diferir unas décimas de segundo.
TOLERANCIA_S = 0.3
COLUMNAS = ["clase_id", "curso", "tema", "inicio_s", "fin_s", "hablante", "texto"]


def fusionar(segmentos: list[dict]) -> list[dict]:
    """Grupos de segmentos que se solapan (en tiempo de clase) entre micrófonos distintos."""
    grupos: list[list[dict]] = []
    for s in sorted(segmentos, key=lambda s: s["inicio_clase_s"]):
        g = grupos[-1] if grupos else None
        if (g and s["mic"] not in {x["mic"] for x in g}
                and s["inicio_clase_s"] <= max(x["fin_clase_s"] for x in g) + TOLERANCIA_S):
            g.append(s)
        else:
            grupos.append([s])
    salida = []
    for g in grupos:
        mejor = max(g, key=lambda x: x["similitud"])
        salida.append({
            "inicio_s": round(min(x["inicio_clase_s"] for x in g), 3),
            "fin_s": round(max(x["fin_clase_s"] for x in g), 3),
            "codigo": mejor["codigo"], "hablante": mejor["nombre"], "similitud": mejor["similitud"],
            "mic": mejor["mic"], "mics": sorted({x["mic"] for x in g}), "indice": mejor.get("i"),
            "por_mic": {x["mic"]: [x["nombre"], x["similitud"]] for x in g},
        })
    return salida


def main():
    carpeta = carpeta_desde_args()
    meta = metadata(carpeta)
    ident = leer_json(carpeta / "identificacion.json", "identificar")
    clase = datos_clase(carpeta, meta)
    inter = fusionar(ident["segmentos"])
    escribir_json(carpeta / "intervenciones.json", [{**clase, **i, "texto": ""} for i in inter])
    with open(carpeta / "intervenciones.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        w.writeheader()
        w.writerows({**clase, **i, "texto": ""} for i in inter)
    varios = sum(len(i["mics"]) > 1 for i in inter)
    desacuerdo = sum(len({n for n, _ in i["por_mic"].values()}) > 1 for i in inter)
    print(f"  {len(ident['segmentos'])} segmentos → {len(inter)} intervenciones "
          f"({varios} oídas por más de un celular, {desacuerdo} con identificación distinta según el celular)")
    print(f"→ {carpeta / 'intervenciones.json'} y intervenciones.csv")


if __name__ == "__main__":
    main()
