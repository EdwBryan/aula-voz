"""Etapa: asistencia por alumno.

    python -m reconocimiento.asistencia [carpeta_clase]   # identificacion.json → asistencia.json y .csv

Un alumno está presente si se le reconoce dentro de la ventana de asistencia
(VENTANA_ASISTENCIA_S; por ahora toda la clase). Las intervenciones juntan los segmentos
seguidos de la misma persona, aunque los hayan oído varios micrófonos.
"""

import csv

from .clase import carpeta_desde_args, escribir_json, leer_json, metadata
from .config import VENTANA_ASISTENCIA_S
from .huellas import Huellas

PAUSA_INTERVENCION_S = 2.0  # elección propia: más de 2 s de silencio separa dos intervenciones
COLUMNAS = ["clase_id", "curso", "tema", "codigo", "nombre", "presente", "primera_vez_s",
            "intervenciones", "voz_s", "mejor_similitud", "microfonos"]


def resumen(segmentos: list[dict], huellas: Huellas, clase: dict) -> list[dict]:
    """Una fila por alumno registrado. segmentos: salida de Huellas.identificar más
    inicio_clase_s, fin_clase_s y mic."""
    filas = []
    for codigo, nombre in zip(huellas.codigos, huellas.nombres):
        suyos = sorted((s for s in segmentos if s["codigo"] == codigo), key=lambda s: s["inicio_clase_s"])
        en_ventana = [s for s in suyos if VENTANA_ASISTENCIA_S is None or s["inicio_clase_s"] <= VENTANA_ASISTENCIA_S]
        intervenciones, voz, fin = 0, 0.0, None
        for s in suyos:
            a, b = s["inicio_clase_s"], s["fin_clase_s"]
            if fin is None or a > fin + PAUSA_INTERVENCION_S:
                intervenciones += 1
            voz += max(0.0, b - max(a, fin or a))  # sin contar dos veces lo que oyeron dos micrófonos
            fin = b if fin is None else max(fin, b)
        filas.append({**clase, "codigo": codigo, "nombre": nombre, "presente": bool(en_ventana),
                      "primera_vez_s": en_ventana[0]["inicio_clase_s"] if en_ventana else None,
                      "intervenciones": intervenciones, "voz_s": round(voz, 1),
                      "mejor_similitud": max((s["similitud"] for s in suyos), default=None),
                      "microfonos": " ".join(sorted({s["mic"] for s in suyos}))})
    return filas


def guardar(carpeta, filas: list[dict], nombre: str = "asistencia"):
    escribir_json(carpeta / f"{nombre}.json", filas)
    with open(carpeta / f"{nombre}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)


def datos_clase(carpeta, meta: dict) -> dict:
    return {"clase_id": carpeta.name, "curso": meta.get("curso", ""), "tema": meta.get("tema", "")}


def main():
    carpeta = carpeta_desde_args()
    meta = metadata(carpeta)
    ident = leer_json(carpeta / "identificacion.json", "identificar")
    filas = resumen(ident["segmentos"], Huellas.cargar(), datos_clase(carpeta, meta))
    guardar(carpeta, filas)
    for f in filas:
        estado = f"presente (desde {f['primera_vez_s']:.0f} s)" if f["presente"] else "ausente"
        print(f"  {f['nombre']:40s} {estado:24s} {f['intervenciones']} intervenciones, {f['voz_s']} s de voz")
    print(f"→ {carpeta / 'asistencia.json'} y asistencia.csv")


if __name__ == "__main__":
    main()
