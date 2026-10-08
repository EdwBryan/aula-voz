"""Reconocimiento en vivo durante la clase: Silero VAD + ECAPA sobre el audio que llega.

- El servidor llama a alimentar() con cada trozo (no bloquea: va a una cola).
- Un hilo aparte procesa la cola: un VAD por micrófono; cuando se cierra un segmento de voz
  (o llega a 6 s) se identifica con las huellas y se marca la asistencia.
- Al terminar la clase se guardan en su carpeta en_vivo.json (cada segmento con su latencia)
  y asistencia_en_vivo.json/.csv. Lo mismo se puede recalcular después con
  python -m reconocimiento.procesar sobre los WAV.
"""

import json
import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

log = logging.getLogger("aula")

VENTANA = 512           # muestras por paso del VAD de Silero a 16 kHz
MEMORIA_S = 8           # audio que se guarda hacia atrás para recortar el segmento


class Mic:
    """Estado del VAD de un micrófono. Las posiciones son muestras dentro de su WAV."""

    def __init__(self, nombre: str, desfase_s: float):
        from reconocimiento.config import FS, SILERO
        from reconocimiento.modelos import nuevo_vad
        from silero_vad import VADIterator

        self.nombre = nombre
        self.desfase_s = desfase_s
        self.iterador = VADIterator(nuevo_vad(), threshold=SILERO["threshold"], sampling_rate=FS,
                                    min_silence_duration_ms=SILERO["min_silence_duration_ms"],
                                    speech_pad_ms=SILERO["speech_pad_ms"])
        self.esperado = None        # siguiente muestra que debería llegar
        self.base = 0               # posición que corresponde a la muestra 0 del iterador
        self.buf = np.zeros(0, dtype=np.float32)
        self.buf_inicio = 0         # posición de buf[0]
        self.resto = np.zeros(0, dtype=np.float32)
        self.inicio_voz = None      # posición donde empezó el segmento abierto
        self.llegada_inicio = None  # hora (s) en que llegó el trozo donde empezó la voz

    def reiniciar(self, pos: int):
        self.iterador.reset_states()
        self.base = pos
        self.buf = np.zeros(0, dtype=np.float32)
        self.buf_inicio = pos
        self.resto = np.zeros(0, dtype=np.float32)
        self.inicio_voz = None

    def recorte(self, a: int, b: int) -> np.ndarray:
        return self.buf[max(a - self.buf_inicio, 0):max(b - self.buf_inicio, 0)]


class EnVivo:
    def __init__(self):
        self.cola: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.estado = "cargando"    # cargando | listo | sin_huellas | error
        self.mensaje = "Cargando modelos de voz…"
        self.huellas = None
        self._reiniciar_sesion(None)
        threading.Thread(target=self._trabajar, name="en-vivo", daemon=True).start()

    # ---------- llamado desde el servidor (bucle de asyncio) ----------

    def nueva_sesion(self, inicio_clase_ms: float):
        self.cola.put(("sesion", inicio_clase_ms))

    def alimentar(self, mic: str, desfase_s: float, pos: int, pcm: bytes):
        self.cola.put(("audio", mic, desfase_s, pos, pcm, time.time()))

    def terminar(self, carpeta: Path | None, clase: dict, espera_s: float = 60) -> bool:
        """Procesa lo que queda en la cola y guarda los resultados. Bloquea: usar con to_thread."""
        listo = threading.Event()
        self.cola.put(("terminar", carpeta, clase, listo))
        return listo.wait(espera_s)

    def resumen(self) -> dict:
        with self.lock:
            h = self.huellas
            presentes = dict(self.presentes)
            lat = [s["latencia_s"] for s in self.segmentos if s.get("latencia_s") is not None]
            return {
                "estado": self.estado,
                "mensaje": self.mensaje,
                "umbral": h.umbral if h else None,
                "alumnos": [{"codigo": c, "nombre": n, **presentes.get(c, {"presente": False})}
                            for c, n in zip(h.codigos, h.nombres)] if h else [],
                "ultimos": list(self.ultimos),
                "segmentos": len(self.segmentos),
                "en_cola": self.cola.qsize(),
                "latencia_media_s": round(float(np.mean(lat)), 2) if lat else None,
                "latencia_max_s": round(float(np.max(lat)), 2) if lat else None,
            }

    # ---------- hilo de trabajo ----------

    def _reiniciar_sesion(self, inicio_ms):
        with self.lock:
            self.inicio_ms = inicio_ms
            self.mics: dict[str, Mic] = {}
            self.segmentos: list[dict] = []
            self.presentes: dict[str, dict] = {}
            self.ultimos: deque = deque(maxlen=12)

    def _cargar(self):
        from reconocimiento.config import HUELLAS
        from reconocimiento.huellas import Huellas
        from reconocimiento.modelos import ecapa, nuevo_vad

        nuevo_vad()
        ecapa()
        try:
            h = Huellas.cargar()
        except FileNotFoundError:
            h = None
        with self.lock:
            self.huellas = h
            if h is None or not h.codigos:
                self.estado = "sin_huellas"
                self.mensaje = (f"No hay huellas en {HUELLAS.name}/: ejecuta python -m reconocimiento.huellas "
                                "después del registro. Se detecta voz pero no se identifica.")
            else:
                self.estado = "listo"
                self.mensaje = f"{len(h.codigos)} alumnos registrados, umbral {h.umbral}"
        log.info("Reconocimiento en vivo: %s", self.mensaje)

    def _trabajar(self):
        try:
            self._cargar()
        except Exception as e:
            log.exception("No se pudieron cargar los modelos de voz")
            with self.lock:
                self.estado, self.mensaje = "error", f"No se pudieron cargar los modelos: {e}"
        while True:
            item = self.cola.get()
            try:
                if item[0] == "audio":
                    if self.estado in ("listo", "sin_huellas"):
                        self._audio(*item[1:])
                elif item[0] == "sesion":
                    if self.estado in ("listo", "sin_huellas"):
                        self._cargar()  # relee las huellas: puede haber registros nuevos
                    self._reiniciar_sesion(item[1])
                elif item[0] == "terminar":
                    _, carpeta, clase, listo = item
                    try:
                        self._cerrar_segmentos()
                        if carpeta is not None:
                            self._guardar(carpeta, clase)
                    finally:
                        listo.set()
            except Exception:
                log.exception("Error en el reconocimiento en vivo")

    def _audio(self, nombre: str, desfase_s: float, pos: int, pcm: bytes, llegada: float):
        from reconocimiento.config import FS, SEGMENTO_MAX_S

        m = self.mics.get(nombre)
        if m is None:
            m = self.mics[nombre] = Mic(nombre, desfase_s)
        if m.esperado is not None and pos < m.esperado:
            return  # reenviado: ya se procesó
        if m.esperado is None or pos > m.esperado:
            # Primer trozo o hueco (pausa, corte de red): el VAD empieza de cero.
            if m.inicio_voz is not None:
                self._cerrar(m, m.esperado, llegada)
            m.reiniciar(pos)
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        m.esperado = pos + len(x)
        m.buf = np.concatenate([m.buf, x])
        guardar_desde = min(m.inicio_voz if m.inicio_voz is not None else m.esperado, m.esperado - int(MEMORIA_S * FS))
        if guardar_desde > m.buf_inicio:
            m.buf = m.buf[guardar_desde - m.buf_inicio:]
            m.buf_inicio = guardar_desde

        import torch
        datos = np.concatenate([m.resto, x])
        n = len(datos) // VENTANA * VENTANA
        m.resto = datos[n:]
        for i in range(0, n, VENTANA):
            ev = m.iterador(torch.from_numpy(datos[i:i + VENTANA]), return_seconds=False)
            if ev and "start" in ev:
                m.inicio_voz = m.base + int(ev["start"])
                m.llegada_inicio = llegada
            elif ev and "end" in ev and m.inicio_voz is not None:
                self._cerrar(m, m.base + int(ev["end"]), llegada)
            # Turno largo: se identifica cada 6 s sin esperar a que termine.
            ahora = m.base + m.iterador.current_sample
            if m.inicio_voz is not None and ahora - m.inicio_voz >= SEGMENTO_MAX_S * FS:
                fin = m.inicio_voz + int(SEGMENTO_MAX_S * FS)
                self._cerrar(m, fin, llegada)
                m.inicio_voz, m.llegada_inicio = fin, llegada

    def _cerrar_segmentos(self):
        for m in self.mics.values():
            if m.inicio_voz is not None and m.esperado is not None:
                self._cerrar(m, m.esperado, time.time())

    def _cerrar(self, m: Mic, fin: int, llegada: float):
        from reconocimiento.config import FS, SEGMENTO_MIN_S
        from reconocimiento.modelos import embedding

        a, m.inicio_voz = m.inicio_voz, None
        if a is None or (fin - a) / FS < SEGMENTO_MIN_S:
            return
        x = m.recorte(a, fin)
        if len(x) < SEGMENTO_MIN_S * FS:
            return
        h = self.huellas
        if h is not None and h.codigos:
            r = h.identificar(embedding(x))
        else:
            r = {"codigo": None, "nombre": "desconocido", "similitud": 0.0, "candidato": None, "segundo": 0.0}
        marcado = time.time()
        seg = {
            "mic": m.nombre, "inicio_s": round(a / FS, 3), "fin_s": round(fin / FS, 3),
            "inicio_clase_s": round(m.desfase_s + a / FS, 3), "fin_clase_s": round(m.desfase_s + fin / FS, 3),
            **r,
            # Desde que llegó el audio que cerró el segmento hasta tener el resultado.
            "latencia_s": round(marcado - llegada, 3),
            # Desde que llegó el comienzo de la voz (incluye lo que dura la frase).
            "desde_inicio_voz_s": round(marcado - m.llegada_inicio, 3) if m.llegada_inicio else None,
            "hora": time.strftime("%H:%M:%S", time.localtime(marcado)),
        }
        nuevo = False
        with self.lock:
            self.segmentos.append(seg)
            self.ultimos.appendleft({k: seg[k] for k in ("hora", "mic", "nombre", "similitud", "candidato",
                                                         "inicio_clase_s", "fin_clase_s", "latencia_s")})
            c = r["codigo"]
            if c:
                p = self.presentes.get(c)
                if p is None:
                    nuevo = True
                    p = self.presentes[c] = {"presente": True, "primera_vez_s": seg["inicio_clase_s"],
                                             "hora": seg["hora"], "veces": 0, "mejor_similitud": 0.0,
                                             "latencia_s": seg["latencia_s"],
                                             "desde_inicio_voz_s": seg["desde_inicio_voz_s"]}
                p["veces"] += 1
                p["mejor_similitud"] = max(p["mejor_similitud"], r["similitud"])
        log.info("  voz %s %6.1f–%6.1f s → %-30s sim %.2f (2º %.2f)  latencia %.2f s%s",
                 m.nombre, seg["inicio_s"], seg["fin_s"], r["nombre"], r["similitud"], r["segundo"],
                 seg["latencia_s"], "  ✓ PRESENTE" if nuevo else "")

    def _guardar(self, carpeta: Path, clase: dict):
        from reconocimiento import asistencia

        with self.lock:
            segs = list(self.segmentos)
            h = self.huellas
        lat = [s["latencia_s"] for s in segs]
        datos = {
            "umbral": h.umbral if h else None,
            "latencia_s": {"media": round(float(np.mean(lat)), 3), "p95": round(float(np.percentile(lat, 95)), 3),
                           "max": round(float(np.max(lat)), 3)} if lat else None,
            "segmentos": segs,
        }
        (carpeta / "en_vivo.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        if h is not None and h.codigos:
            asistencia.guardar(carpeta, asistencia.resumen(segs, h, clase), "asistencia_en_vivo")
        log.info("Reconocimiento en vivo guardado: %d segmentos, %d presentes",
                 len(segs), len({s["codigo"] for s in segs if s["codigo"]}))
