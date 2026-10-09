"""Etapa: aprender las voces de la lista que pasa el docente.

    python -m reconocimiento.lista [carpeta_clase] [--alumnos data/alumnos.csv]   # → lista.json y lista.csv

El docente dice un nombre y el alumno responde "presente". Esta etapa:
1. transcribe (faster-whisper, español, sin internet) el celular que mejor oye al docente,
   dentro de las marcas de la sesión si las hay (la primera y la última);
2. separa cada llamada (el nombre) de las respuestas ("presente", "aquí");
3. busca la respuesta en el hueco después de cada nombre, en todos los celulares, y se
   queda con el que mejor la oyó; de ahí sale el embedding ECAPA de esa voz;
4. si hay lista del curso (CSV codigo,nombre), empareja el nombre transcrito con ella;
5. avisa si la misma voz respondió por dos nombres distintos (posible suplantación).

No crea huellas: el resultado se revisa primero (lista.csv) y luego
python -m reconocimiento.huellas_lista crea las huellas de quienes estén confirmados.
"""

import argparse
import csv
import difflib
import re
import unicodedata
from pathlib import Path

import numpy as np

from . import audio
from .clase import desfases, escribir_json, metadata
from .config import CLASES, DATOS, FS, MODELOS
from .modelos import embedding

RESPUESTAS = ("presente", "aqui", "aca", "presentes")
# Elección propia: una llamada termina si el docente se calla más de 0.8 s.
PAUSA_LLAMADA_S = 0.8
# Elección propia: la respuesta se busca hasta 4 s después del nombre.
ESPERA_RESPUESTA_S = 4.0
# Elección propia: dos respuestas con similitud ≥ 0.65 se consideran la misma voz.
MISMA_VOZ = 0.65
# Elección propia: una "respuesta" muy parecida al docente es el docente repitiendo.
PARECIDO_DOCENTE = 0.5


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]+", " ", t).strip()


def es_respuesta(palabra: str) -> bool:
    p = normalizar(palabra)
    return any(difflib.SequenceMatcher(None, p, r).ratio() >= 0.75 for r in RESPUESTAS)


def transcribir(x: np.ndarray):
    from faster_whisper import WhisperModel
    modelo = WhisperModel("small", device="cpu", compute_type="int8", download_root=str(MODELOS / "whisper"))
    segs, _ = modelo.transcribe(x, language="es", word_timestamps=True, vad_filter=True,
                                initial_prompt="El profesor pasa lista: dice nombres y apellidos y los alumnos responden presente.")
    return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or [])]


def leer_alumnos(ruta: Path | None) -> list[dict]:
    if not ruta or not ruta.exists():
        return []
    with open(ruta, encoding="utf-8") as f:
        return [{"codigo": r["codigo"].strip(), "nombre": r["nombre"].strip()} for r in csv.DictReader(f)]


def emparejar(texto: str, alumnos: list[dict]) -> tuple[dict | None, float]:
    """Alumno de la lista cuyo nombre más se parece a lo que dijo el docente."""
    t = normalizar(texto)
    mejor, puntaje = None, 0.0
    for a in alumnos:
        n = normalizar(a["nombre"])
        # el docente suele decir solo apellidos: se compara contra cada sub-secuencia de palabras
        partes = n.split()
        candidatos = {" ".join(partes[i:j]) for i in range(len(partes)) for j in range(i + 1, len(partes) + 1)}
        p = max(difflib.SequenceMatcher(None, t, c).ratio() for c in candidatos)
        if p > puntaje:
            mejor, puntaje = a, p
    return mejor, round(puntaje, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("carpeta", nargs="?")
    ap.add_argument("--alumnos", type=Path, default=DATOS / "alumnos.csv", help="CSV con columnas codigo,nombre")
    a = ap.parse_args()
    carpeta = (Path(a.carpeta) if a.carpeta and Path(a.carpeta).is_dir() else CLASES / a.carpeta) if a.carpeta \
        else sorted(p for p in CLASES.iterdir() if (p / "metadata.json").exists())[-1]
    meta = metadata(carpeta)
    desf = desfases(meta)
    alumnos = leer_alumnos(a.alumnos)

    # Ventana: de la primera a la última marca (en tiempo de clase); sin marcas, toda la clase.
    marcas = sorted(m["t_ms"] for m in meta.get("marcas", []))
    inicio_ms = next((e["t_ms"] for e in meta["eventos"] if e["tipo"] == "inicio"), None)
    if len(marcas) >= 2 and inicio_ms:
        desde, hasta = (marcas[0] - inicio_ms) / 1000 - 2, (marcas[-1] - inicio_ms) / 1000 + 2
    elif len(marcas) == 1 and inicio_ms:
        desde, hasta = (marcas[0] - inicio_ms) / 1000 - 2, None
    else:
        desde, hasta = 0.0, None

    wavs = {m: audio.leer(carpeta / d["archivo"]) for m, d in meta["dispositivos"].items()}

    def tramo(mic, a_clase, b_clase):
        x = wavs[mic]
        a = max(0, int((a_clase - desf[mic]) * FS))
        b = len(x) if b_clase is None else min(len(x), int((b_clase - desf[mic]) * FS))
        return x[a:b], a / FS + desf[mic]

    # El celular que mejor oye al docente: el que tiene más voz en la ventana.
    voz_mic = {}
    for m in wavs:
        x, _ = tramo(m, desde, hasta)
        voz_mic[m] = sum(b - a for a, b in audio.voz(x))
    mic_docente = max(voz_mic, key=voz_mic.get)
    x, t0 = tramo(mic_docente, desde, hasta)
    print(f"  Ventana de la lista: {desde:.0f} s a {'fin' if hasta is None else f'{hasta:.0f} s'}; "
          f"transcribiendo {mic_docente} ({len(x) / FS:.0f} s de audio)…")
    palabras = [(t0 + s, t0 + e, w) for s, e, w in transcribir(x)]
    if not palabras:
        raise SystemExit("La transcripción no encontró palabras en la ventana.")

    # Llamadas: palabras seguidas del docente que no son respuestas.
    llamadas, actual = [], []
    for s, e, w in palabras:
        if es_respuesta(w):
            if actual:
                llamadas.append(actual)
                actual = []
            continue
        if actual and s - actual[-1][1] > PAUSA_LLAMADA_S:
            llamadas.append(actual)
            actual = []
        actual.append((s, e, w))
    if actual:
        llamadas.append(actual)
    respuestas_whisper = [(s, e) for s, e, w in palabras if es_respuesta(w)]

    # Voz del docente: promedio de sus llamadas (para descartar "respuestas" que son él).
    emb_doc = [embedding(tramo(mic_docente, l[0][0], l[-1][1])[0]) for l in llamadas if l[-1][1] - l[0][0] >= 0.8]
    centro_doc = None
    if emb_doc:
        centro_doc = np.mean(emb_doc, axis=0)
        centro_doc /= np.linalg.norm(centro_doc)

    filas = []
    for i, l in enumerate(llamadas):
        texto = " ".join(w for _, _, w in l)
        fin_nombre = l[-1][1]
        limite = min(fin_nombre + ESPERA_RESPUESTA_S, llamadas[i + 1][0][0] if i + 1 < len(llamadas) else 1e9)
        # Candidatos: voz en cualquier celular dentro del hueco.
        cands = []
        for m in wavs:
            y, ty = tramo(m, fin_nombre, limite)
            for va, vb in audio.voz(y):
                seg = y[int(va * FS):int(vb * FS)]
                if len(seg) < 0.2 * FS:
                    continue
                pico = float(np.percentile(np.abs(seg), 99))
                cands.append({"mic": m, "inicio": ty + va, "fin": ty + vb, "pico": pico, "audio": seg})
        # Si whisper oyó "presente" en el hueco, se prefiere lo que se solapa con eso.
        dicho = [r for r in respuestas_whisper if fin_nombre <= r[0] < limite]
        fila = {"n": i + 1, "llamada": texto, "nombre_s": round(l[0][0], 2), "fin_nombre_s": round(fin_nombre, 2),
                "whisper_oyo_respuesta": bool(dicho)}
        if alumnos:
            al, p = emparejar(texto, alumnos)
            fila.update({"codigo": al["codigo"] if al and p >= 0.6 else None,
                         "alumno": al["nombre"] if al and p >= 0.6 else None, "parecido_nombre": p})
        if cands:
            if dicho:
                solapan = [c for c in cands if c["inicio"] < dicho[0][1] and c["fin"] > dicho[0][0]]
                cands = solapan or cands
            c = max(cands, key=lambda c: c["pico"])
            e = embedding(c["audio"])
            sim_doc = float(e @ centro_doc) if centro_doc is not None else None
            fila.update({"respuesta_s": round(c["inicio"], 2), "respuesta_fin_s": round(c["fin"], 2), "mic": c["mic"],
                         "parecido_docente": round(sim_doc, 2) if sim_doc is not None else None,
                         "_emb": e})
            fila["estado"] = "es el docente" if sim_doc is not None and sim_doc >= PARECIDO_DOCENTE else "respuesta"
        else:
            fila["estado"] = "sin respuesta"
        filas.append(fila)

    # Misma voz respondiendo por dos nombres.
    con = [f for f in filas if f.get("estado") == "respuesta"]
    for i, f in enumerate(con):
        for g in con[i + 1:]:
            s = float(f["_emb"] @ g["_emb"])
            if s >= MISMA_VOZ:
                for x_, y_ in ((f, g), (g, f)):
                    x_.setdefault("alerta", []).append(f"misma voz que la respuesta a «{y_['llamada']}» ({s:.2f})")

    E = np.array([f["_emb"] for f in con]).reshape(len(con), -1)
    np.savez(carpeta / "lista_embeddings.npz", E=E, n=np.array([f["n"] for f in con]))
    for f in filas:
        f.pop("_emb", None)
        if "alerta" in f:
            f["alerta"] = "; ".join(f["alerta"])
    escribir_json(carpeta / "lista.json", {"mic_docente": mic_docente, "ventana_s": [desde, hasta],
                                            "transcripcion": " ".join(w for _, _, w in palabras), "llamadas": filas})
    columnas = ["n", "llamada", "alumno", "codigo", "parecido_nombre", "estado", "nombre_s", "respuesta_s",
                "respuesta_fin_s", "mic", "parecido_docente", "whisper_oyo_respuesta", "alerta"]
    with open(carpeta / "lista.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columnas, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)

    print(f"  {len(llamadas)} llamadas; {len(con)} con respuesta; "
          f"{sum(1 for f in filas if f['estado'] == 'sin respuesta')} sin respuesta; "
          f"{sum(1 for f in filas if f['estado'] == 'es el docente')} descartadas (voz del docente); "
          f"{sum(1 for f in filas if f.get('alerta'))} con alerta de misma voz")
    for f in filas[:60]:
        print(f"   {f['n']:3d} {f['llamada'][:40]:40s} → {f['estado']:14s} {f.get('mic', ''):6s} "
              f"{f.get('alumno') or ''} {('⚠ ' + f['alerta']) if f.get('alerta') else ''}")
    print(f"→ {carpeta / 'lista.csv'}")


if __name__ == "__main__":
    main()
