"""Validación y guardado de audio: siempre WAV PCM 16 bits, mono, 16 kHz."""

from pathlib import Path

import numpy as np
import soundfile as sf

FS = 16000
CANALES = 1
SUBTIPO = "PCM_16"

# Criterios para aceptar una toma de registro.
# El navegador usa los mismos valores (static/js/registro.js); el servidor decide.
DURACION_MIN_S = 2.0
DURACION_MAX_S = 5.0     # el celular corta solo a los 5 s
VOZ_MIN_S = 0.8            # tiempo mínimo con voz por encima del ruido de fondo
NIVEL_MIN_DBFS = -42.0     # el 5 % más fuerte de la toma debe superar esto
SATURACION_MAX = 0.005     # fracción máxima de muestras recortadas (al tope)
TRAMA = 320                # 20 ms a 16 kHz


def pcm_desde_bytes(datos: bytes) -> np.ndarray:
    if len(datos) % 2:
        raise ValueError("el audio tiene un número impar de bytes")
    return np.frombuffer(datos, dtype="<i2")


def evaluar_toma(pcm: np.ndarray) -> dict:
    """Mide la toma y dice si sirve. Devuelve un dict serializable a JSON."""
    duracion = len(pcm) / FS
    x = pcm.astype(np.float32) / 32768.0
    n = len(x) // TRAMA
    if n == 0:
        return {"ok": False, "motivo": "La toma está vacía.", "duracion_s": round(duracion, 2)}

    tramas = x[: n * TRAMA].reshape(n, TRAMA)
    db = 20 * np.log10(np.sqrt((tramas**2).mean(axis=1)) + 1e-9)
    piso = float(np.percentile(db, 10))
    fuerte = float(np.percentile(db, 95))
    voz_s = float((db > max(piso + 12, -50)).sum() * TRAMA / FS)
    saturacion = float((np.abs(pcm.astype(np.int32)) >= 32700).mean())

    medidas = {
        "duracion_s": round(duracion, 2),
        "nivel_dbfs": round(fuerte, 1),
        "ruido_fondo_dbfs": round(piso, 1),
        "voz_s": round(voz_s, 2),
        "saturacion": round(saturacion, 4),
    }
    motivo = None
    if duracion < DURACION_MIN_S:
        motivo = f"Muy corta ({duracion:.1f} s). Lee la frase completa antes de detener."
    elif duracion > DURACION_MAX_S:
        motivo = f"Muy larga ({duracion:.1f} s)."
    elif fuerte < NIVEL_MIN_DBFS:
        motivo = "Muy silenciosa. Habla más fuerte o acerca el celular."
    elif voz_s < VOZ_MIN_S:
        motivo = "Casi no se oye voz. Lee la frase completa en voz alta."
    elif saturacion > SATURACION_MAX:
        motivo = "El sonido satura. Aleja un poco el celular de la boca."
    return {"ok": motivo is None, "motivo": motivo, **medidas}


def guardar_wav(ruta: Path, pcm: np.ndarray) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    sf.write(ruta, pcm, FS, subtype=SUBTIPO, format="WAV")


def verificar_wav(ruta: Path) -> dict:
    """Relee el archivo con soundfile y comprueba que sea 16 kHz, mono, PCM_16."""
    info = sf.info(ruta)
    ok = info.samplerate == FS and info.channels == CANALES and info.subtype == SUBTIPO and info.format == "WAV"
    return {
        "archivo": str(ruta),
        "ok": ok,
        "frecuencia_hz": info.samplerate,
        "canales": info.channels,
        "formato": info.format,
        "subtipo": info.subtype,
        "duracion_s": round(info.duration, 2),
    }


def porcentaje_ceros(ruta: Path, excluir: list[tuple[int, int]] = ()) -> float:
    """% de muestras en cero exacto, sin contar los tramos excluidos (pausas y huecos).

    Un micrófono real siempre tiene algo de ruido; muchos ceros exactos significan que
    el celular está aplicando una compuerta de ruido (silencia lo que no es voz cercana).
    """
    ceros = total = pos = 0
    for bloque in sf.blocks(ruta, blocksize=FS * 60, dtype="int16"):
        valido = np.ones(len(bloque), dtype=bool)
        for a, b in excluir:
            ia, ib = max(a - pos, 0), min(b - pos, len(bloque))
            if ia < ib:
                valido[ia:ib] = False
        ceros += int(np.count_nonzero((bloque == 0) & valido))
        total += int(valido.sum())
        pos += len(bloque)
    return round(100 * ceros / total, 1) if total else 0.0
