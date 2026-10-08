"""Utilidades para una carpeta de clase (data/clase/<fecha>_<curso>/)."""

import json
import sys
from pathlib import Path

from .config import CLASES


def carpeta_desde_args() -> Path:
    """La carpeta pasada como argumento, o la clase más reciente."""
    if len(sys.argv) > 1:
        c = Path(sys.argv[1])
        return c if c.is_dir() else CLASES / sys.argv[1]
    clases = sorted(p for p in CLASES.iterdir() if (p / "metadata.json").exists())
    if not clases:
        sys.exit("No hay clases en data/clase/.")
    return clases[-1]


def metadata(carpeta: Path) -> dict:
    return json.loads((carpeta / "metadata.json").read_text(encoding="utf-8"))


def desfases(meta: dict) -> dict[str, float]:
    """Segundos entre el inicio de la clase y el inicio del WAV de cada micrófono.
    t_clase = desfase[mic] + segundo_dentro_del_wav."""
    inicio = next((e["t_ms"] for e in meta["eventos"] if e["tipo"] == "inicio"), None)
    return {m: (d["inicio_ms"] - inicio) / 1000 if inicio else 0.0 for m, d in meta["dispositivos"].items()}


def escribir_json(ruta: Path, datos):
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def leer_json(ruta: Path, etapa: str):
    if not ruta.exists():
        sys.exit(f"Falta {ruta.name}: ejecuta antes  python -m reconocimiento.{etapa} {ruta.parent}")
    return json.loads(ruta.read_text(encoding="utf-8"))
