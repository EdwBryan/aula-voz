import re
import time
import unicodedata
from datetime import datetime


def ahora_ms() -> float:
    """Hora del servidor en milisegundos (época Unix)."""
    return time.time() * 1000


def iso(ms: float | None = None) -> str:
    t = ahora_ms() if ms is None else ms
    return datetime.fromtimestamp(t / 1000).astimezone().isoformat(timespec="milliseconds")


def limpiar_nombre(texto: str) -> str:
    """'María José Ñique' -> 'Maria_Jose_Nique' (para nombres de carpetas y archivos)."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", sin_tildes).strip("_")
