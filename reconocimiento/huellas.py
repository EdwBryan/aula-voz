"""Etapa: huellas de voz a partir del registro.

    python -m reconocimiento.huellas            # data/registro/ → data/huellas/

Huella = promedio (normalizado) de los embeddings ECAPA de las tomas del alumno, usando
solo la parte con voz de cada toma. El umbral de "desconocido" se calibra con el mismo
registro: cada toma se compara con la huella de su dueño hecha sin ella (propias) y con
las huellas de los demás (ajenas).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audio
from .config import HUELLAS, METRICAS, REGISTRO, UMBRAL_MAXIMO, UMBRAL_POR_DEFECTO
from .modelos import embedding


@dataclass
class Huellas:
    codigos: list[str]
    nombres: list[str]
    matriz: np.ndarray      # (alumnos, 192), cada fila con norma 1
    cuenta: np.ndarray      # cuántos segmentos forman cada huella
    umbral: float

    def nombre(self, codigo: str | None) -> str:
        return self.nombres[self.codigos.index(codigo)] if codigo in self.codigos else "desconocido"

    def identificar(self, v: np.ndarray) -> dict:
        """El alumno más parecido; 'desconocido' (codigo None) si no pasa el umbral."""
        if not self.codigos:
            return {"codigo": None, "nombre": "desconocido", "similitud": 0.0, "candidato": None, "segundo": 0.0}
        sims = self.matriz @ v
        orden = np.argsort(sims)[::-1]
        i = int(orden[0])
        mejor = float(sims[i])
        segundo = float(sims[orden[1]]) if len(orden) > 1 else -1.0
        codigo = self.codigos[i] if mejor >= self.umbral else None
        return {"codigo": codigo, "nombre": self.nombre(codigo), "similitud": round(mejor, 4),
                "candidato": self.codigos[i], "segundo": round(segundo, 4)}

    def guardar(self, carpeta: Path = HUELLAS):
        carpeta.mkdir(parents=True, exist_ok=True)
        np.savez(carpeta / "huellas.npz", matriz=self.matriz, cuenta=self.cuenta)
        (carpeta / "alumnos.json").write_text(json.dumps(
            {"umbral": self.umbral, "alumnos": [{"codigo": c, "nombre": n, "segmentos": int(k)}
                                                for c, n, k in zip(self.codigos, self.nombres, self.cuenta)]},
            ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def cargar(cls, carpeta: Path = HUELLAS) -> "Huellas":
        info = json.loads((carpeta / "alumnos.json").read_text(encoding="utf-8"))
        arr = np.load(carpeta / "huellas.npz")
        return cls([a["codigo"] for a in info["alumnos"]], [a["nombre"] for a in info["alumnos"]],
                   arr["matriz"], arr["cuenta"], float(info["umbral"]))


def normalizar(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def embedding_toma(ruta: Path) -> np.ndarray:
    """Embedding de la parte con voz de una toma (si el VAD no ve voz, la toma entera)."""
    x = audio.leer(ruta)
    tramos = audio.voz(x)
    if tramos:
        x = np.concatenate([audio.recorte(x, a, b) for a, b in tramos])
    return embedding(x)


def leer_registro(carpeta: Path = REGISTRO) -> dict[str, dict]:
    """{codigo: {nombre, embeddings (tomas, 192)}} de cada alumno registrado."""
    alumnos = {}
    for d in sorted(p for p in carpeta.iterdir() if p.is_dir() and not p.name.startswith(".")):
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        tomas = [d / t["archivo"] for t in meta["tomas"]]
        alumnos[meta["codigo"]] = {"nombre": meta["nombre"], "embeddings": np.array([embedding_toma(t) for t in tomas])}
        print(f"  {meta['codigo']}  {meta['nombre']}: {len(tomas)} tomas")
    return alumnos


def calibrar(alumnos: dict[str, dict]) -> dict:
    """Puntuaciones dejando-una-fuera y umbral que mejor separa propias de ajenas."""
    codigos = list(alumnos)
    propias, ajenas, aciertos, total = [], [], 0, 0
    for c in codigos:
        E = alumnos[c]["embeddings"]
        for i in range(len(E)):
            if len(E) < 2:
                continue
            centros = {m: normalizar((np.delete(E, i, 0) if m == c else alumnos[m]["embeddings"]).mean(0))
                       for m in codigos}
            pts = {m: float(E[i] @ centros[m]) for m in codigos}
            aciertos += max(pts, key=pts.get) == c
            total += 1
            propias.append(pts[c])
            ajenas += [pts[m] for m in codigos if m != c]

    if not propias or not ajenas:
        umbral, metodo = UMBRAL_POR_DEFECTO, "por defecto (hacen falta al menos 2 alumnos)"
    elif min(propias) > max(ajenas):
        umbral, metodo = (min(propias) + max(ajenas)) / 2, "punto medio entre propias y ajenas"
    else:
        cand = np.sort(propias + ajenas)
        err = [np.sum(np.array(propias) < u) + np.sum(np.array(ajenas) >= u) for u in cand]
        umbral, metodo = float(cand[int(np.argmin(err))]), "menor número de errores"
    umbral_registro = round(float(umbral), 4)
    if umbral > UMBRAL_MAXIMO:
        umbral, metodo = UMBRAL_MAXIMO, f"tope UMBRAL_MAXIMO (el registro daba {umbral_registro}, {metodo})"
    return {"umbral": round(float(umbral), 4), "metodo": metodo, "umbral_registro": umbral_registro,
            "acierto_dejando_una_fuera": f"{aciertos}/{total}",
            "propias": [round(p, 4) for p in propias], "ajenas": [round(a, 4) for a in ajenas],
            "propias_min": round(min(propias), 4) if propias else None,
            "ajenas_max": round(max(ajenas), 4) if ajenas else None}


def construir(alumnos: dict[str, dict], umbral: float) -> Huellas:
    codigos = list(alumnos)
    return Huellas(codigos, [alumnos[c]["nombre"] for c in codigos],
                   np.array([normalizar(alumnos[c]["embeddings"].mean(0)) for c in codigos]).reshape(len(codigos), -1),
                   np.array([len(alumnos[c]["embeddings"]) for c in codigos]), umbral)


def main():
    print(f"Leyendo el registro de {REGISTRO}")
    alumnos = leer_registro()
    cal = calibrar(alumnos)
    h = construir(alumnos, cal["umbral"])
    h.guardar()
    METRICAS.mkdir(parents=True, exist_ok=True)
    archivo = METRICAS / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_calibracion.json"
    archivo.write_text(json.dumps({"alumnos": len(h.codigos), **cal}, indent=2), encoding="utf-8")
    print(f"\n{len(h.codigos)} huellas en {HUELLAS}")
    print(f"Acierto dentro del registro (dejando una fuera): {cal['acierto_dejando_una_fuera']}")
    print(f"Similitud con su propia huella: mín {cal['propias_min']}   con otra: máx {cal['ajenas_max']}")
    print(f"Umbral: {cal['umbral']} ({cal['metodo']})   métricas: {archivo.relative_to(METRICAS.parent.parent)}")


if __name__ == "__main__":
    main()
