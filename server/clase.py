"""Modo clase: una sesión (una clase), varios celulares grabando a la vez.

Ideas clave:
- Cada celular graba en "tramos": un tramo es audio continuo, sin cortes del lado del
  celular (de iniciar a pausar, por ejemplo). Cada trozo de audio lleva su tramo, su
  número de secuencia y su posición (muestra) dentro del tramo.
- El servidor escribe cada trozo directamente en su posición dentro del WAV, que está en
  disco: nada se acumula en memoria. Lo que nunca llega queda en ceros (silencio), así que
  el segundo N del WAV es siempre el segundo N desde que empezó ese celular.
- Las pausas y los huecos se calculan comparando lo esperado con lo recibido.
"""

import asyncio
import json
import logging
import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import WebSocket

from . import audio
from .util import ahora_ms, iso, limpiar_nombre

FS = audio.FS
CABECERA_TROZO = struct.Struct("<III")  # tramo, seq, muestra (uint32 little-endian)
ESPERA_CIERRE_S = 15                    # al terminar, cuánto esperar a que los celulares vacíen su audio
ACK_CADA = 10                           # confirmar al celular cada 10 trozos (~1 s)

log = logging.getLogger("aula")


class EscritorWav:
    """WAV PCM 16 bits mono 16 kHz que se escribe por posición a medida que llega el audio.

    Escribir más allá del final rellena con ceros. La cabecera se actualiza cada pocos
    segundos para que el archivo sea válido aunque el servidor se caiga a la mitad.
    """

    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.f = open(ruta, "w+b")
        self.fin = 0  # muestras
        self._cabecera()

    def _cabecera(self):
        datos = self.fin * 2
        self.f.seek(0)
        self.f.write(
            b"RIFF" + struct.pack("<I", 36 + datos) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, FS, FS * 2, 2, 16)
            + b"data" + struct.pack("<I", datos)
        )

    def escribir(self, muestra: int, pcm: bytes):
        self.f.seek(44 + muestra * 2)
        self.f.write(pcm)
        self.fin = max(self.fin, muestra + len(pcm) // 2)

    def extender(self, muestras: int):
        if muestras > self.fin:
            self.f.truncate(44 + muestras * 2)
            self.fin = muestras

    def guardar(self):
        self._cabecera()
        self.f.flush()
        os.fsync(self.f.fileno())

    def cerrar(self):
        if not self.f.closed:
            self.guardar()
            self.f.close()


class Cobertura:
    """Intervalos [a, b) de muestras recibidas, ordenados y sin solaparse."""

    def __init__(self):
        self.iv: list[list[int]] = []

    def agregar(self, a: int, b: int):
        iv = self.iv
        if not iv or a > iv[-1][1]:
            iv.append([a, b])
        elif a >= iv[-1][0]:
            iv[-1][1] = max(iv[-1][1], b)
        else:  # llegó algo viejo (reenvío): insertar y fusionar
            iv.append([a, b])
            iv.sort()
            fusion = [iv[0]]
            for x, y in iv[1:]:
                if x <= fusion[-1][1]:
                    fusion[-1][1] = max(fusion[-1][1], y)
                else:
                    fusion.append([x, y])
            self.iv = fusion

    def total(self) -> int:
        return sum(b - a for a, b in self.iv)

    def faltantes(self, a: int, b: int) -> list[tuple[int, int]]:
        res, cur = [], a
        for x, y in self.iv:
            if y <= cur:
                continue
            if x >= b:
                break
            if x > cur:
                res.append((cur, x))
            cur = max(cur, y)
        if cur < b:
            res.append((cur, b))
        return res


@dataclass
class Tramo:
    id: int
    motivo: str          # inicio | reanudar | reconexion
    t_inicio_ms: float   # hora del servidor de la primera muestra (estimada por el celular)
    pos: int             # muestra del WAV donde empieza
    muestras_fin: int | None = None  # total que dijo el celular al cerrar el tramo
    t_fin_ms: float | None = None
    recibido_hasta: int = 0
    seq_siguiente: int = 0
    seq_adelantados: set = field(default_factory=set)
    deriva: list = field(default_factory=list)  # [s desde inicio, muestras, reloj audio s, saltados, ritmo]

    def largo(self) -> int:
        return self.muestras_fin if self.muestras_fin is not None else self.recibido_hasta


class Grabacion:
    """Lo que un dispositivo grabó dentro de una sesión."""

    def __init__(self, nombre: str, ruta: Path, inicio_ms: float, disp: "Dispositivo"):
        self.nombre = nombre
        self.escritor = EscritorWav(ruta)
        self.cobertura = Cobertura()
        self.tramos: dict[int, Tramo] = {}
        self.inicio_ms = inicio_ms
        self.ultimo_audio_ms: float | None = None
        self.info = {"modelo": disp.info.get("modelo"), "user_agent": disp.info.get("user_agent")}
        self.microfono = disp.microfono
        self.reloj = dict(disp.reloj)
        self.bateria_inicio = disp.bateria
        self.bateria_fin = disp.bateria
        self.verificacion: dict | None = None
        self.duracion_esperada_s: float | None = None

    def tramo_abierto(self) -> Tramo | None:
        for t in reversed(list(self.tramos.values())):
            return t if t.muestras_fin is None else None
        return None

    def fin_esperado(self) -> int:
        return max((t.pos + t.largo() for t in self.tramos.values()), default=0)

    def pausas_y_huecos(self) -> tuple[list, list]:
        pausas, huecos = [], []
        previo = 0
        for t in sorted(self.tramos.values(), key=lambda t: t.pos):
            if t.pos > previo:
                tramo_vacio = {"desde_muestra": previo, "muestras": t.pos - previo,
                               "desde_s": round(previo / FS, 3), "duracion_s": round((t.pos - previo) / FS, 3),
                               "hora": iso(self.inicio_ms + previo * 1000 / FS)}
                if t.motivo == "reanudar":
                    pausas.append(tramo_vacio)
                else:
                    huecos.append({**tramo_vacio, "causa": "el celular dejó de grabar y volvió a empezar"})
            fin = t.pos + t.largo()
            for a, b in self.cobertura.faltantes(t.pos, fin):
                huecos.append({"desde_muestra": a, "muestras": b - a,
                               "desde_s": round(a / FS, 3), "duracion_s": round((b - a) / FS, 3),
                               "hora": iso(self.inicio_ms + a * 1000 / FS), "causa": "trozos que no llegaron"})
            previo = max(previo, fin)
        return pausas, huecos

    def resumen(self) -> dict:
        pausas, huecos = self.pausas_y_huecos()
        return {
            "archivo": self.escritor.ruta.name,
            "inicio": iso(self.inicio_ms),
            "inicio_ms": round(self.inicio_ms, 1),
            **self.info,
            "microfono": self.microfono,
            "reloj": self.reloj,
            "bateria_inicio": self.bateria_inicio,
            "bateria_fin": self.bateria_fin,
            "tramos": [
                {"id": t.id, "motivo": t.motivo, "inicio": iso(t.t_inicio_ms),
                 "desde_muestra": t.pos, "muestras": t.largo(),
                 "desde_s": round(t.pos / FS, 3), "duracion_s": round(t.largo() / FS, 3),
                 "cerrado": t.muestras_fin is not None,
                 "deriva": t.deriva}
                for t in self.tramos.values()
            ],
            "audio_recibido_s": round(self.cobertura.total() / FS, 3),
            "pausas": pausas,
            "huecos": huecos,
            "huecos_total_s": round(sum(h["duracion_s"] for h in huecos), 3),
            "duracion_esperada_s": self.duracion_esperada_s,
            "verificacion": self.verificacion,
        }


@dataclass
class Dispositivo:
    """Un celular conectado (o que estuvo conectado) con su nombre."""
    nombre: str
    cliente_id: str
    ws: WebSocket | None = None
    info: dict = field(default_factory=dict)
    microfono: dict = field(default_factory=dict)
    bateria: dict | None = None
    nivel_db: float = -120.0
    pico: float = 0.0
    pendientes_s: float = 0.0   # audio que el celular aún no pudo mandar (lo informa él)
    ceros: float = 0.0          # fracción de muestras en cero exacto (compuerta del celular)
    reloj: dict = field(default_factory=dict)


class Sesion:
    def __init__(self, curso: str, tema: str, raiz: Path):
        self.curso = curso
        self.tema = tema
        self.raiz = raiz
        self.creada_ms = ahora_ms()
        self.carpeta: Path | None = None
        self.estado = "preparada"  # preparada | grabando | pausada | terminando | terminada
        self.eventos: list[dict] = []
        self.marcas: list[dict] = []
        self.grabaciones: dict[str, Grabacion] = {}
        self.inicio_ms: float | None = None

    def evento(self, tipo: str):
        t = ahora_ms()
        self.eventos.append({"tipo": tipo, "hora": iso(t), "t_ms": round(t, 1)})
        if tipo == "inicio":
            self.inicio_ms = round(t, 1)

    def crear_carpeta(self):
        fecha = datetime.fromtimestamp(ahora_ms() / 1000).strftime("%Y-%m-%d_%H-%M")
        base = self.raiz / f"{fecha}_{limpiar_nombre(self.curso) or 'clase'}"
        carpeta, n = base, 2
        while carpeta.exists():
            carpeta, n = base.with_name(f"{base.name}-{n}"), n + 1
        carpeta.mkdir(parents=True)
        self.carpeta = carpeta

    def segundos(self) -> tuple[float, float]:
        """(tiempo grabando sin contar pausas, tiempo total desde el inicio)."""
        grabando, desde, primero = 0.0, None, None
        for e in self.eventos:
            if e["tipo"] in ("inicio", "reanudar"):
                desde = e["t_ms"]
                primero = primero or desde
            elif e["tipo"] in ("pausa", "fin") and desde is not None:
                grabando += e["t_ms"] - desde
                desde = None
        ahora = ahora_ms()
        if desde is not None:
            grabando += ahora - desde
        fin = next((e["t_ms"] for e in self.eventos if e["tipo"] == "fin"), ahora)
        return grabando / 1000, (fin - primero) / 1000 if primero else 0.0

    def metadata(self) -> dict:
        grabando_s, total_s = self.segundos()
        inicio = next((e["hora"] for e in self.eventos if e["tipo"] == "inicio"), None)
        fin = next((e["hora"] for e in self.eventos if e["tipo"] == "fin"), None)
        return {
            "curso": self.curso,
            "tema": self.tema,
            "estado": self.estado,
            "creada": iso(self.creada_ms),
            "inicio": inicio,
            "fin": fin,
            "tiempo_grabando_s": round(grabando_s, 1),
            "tiempo_total_s": round(total_s, 1),
            "audio": {"frecuencia_hz": FS, "canales": 1, "formato": "WAV", "subtipo": audio.SUBTIPO},
            "notas": "Pausas y huecos van en silencio dentro del WAV: el segundo N del archivo es el "
                     "segundo N desde el 'inicio' de ese dispositivo (hora del servidor). "
                     "Las posiciones exactas están en muestras (*_muestra, 16000 por segundo); "
                     "los *_s están redondeados a milisegundos.",
            "eventos": self.eventos,
            "marcas": self.marcas,
            "dispositivos": {n: g.resumen() for n, g in self.grabaciones.items()},
        }

    def guardar_metadata(self):
        if self.carpeta is None:
            return
        tmp = self.carpeta / "metadata.json.tmp"
        tmp.write_text(json.dumps(self.metadata(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.carpeta / "metadata.json")


class EstadoClase:
    def __init__(self, raiz: Path, en_vivo=None):
        self.raiz = raiz
        self.en_vivo = en_vivo  # reconocimiento en vivo (server/en_vivo.py); None si no está instalado
        self.sesion: Sesion | None = None
        self.dispositivos: dict[str, Dispositivo] = {}
        self.paneles: set[WebSocket] = set()

    # ---------- envío ----------

    async def enviar(self, ws: WebSocket | None, msg: dict):
        if ws is None:
            return
        try:
            await ws.send_json(msg)
        except Exception:
            pass  # se desconectó; lo detecta su propio bucle

    async def a_celulares(self, msg: dict):
        await asyncio.gather(*(self.enviar(d.ws, msg) for d in self.dispositivos.values() if d.ws))

    def info_sesion(self) -> dict | None:
        s = self.sesion
        return None if s is None else {"curso": s.curso, "tema": s.tema, "estado": s.estado}

    # ---------- celulares ----------

    async def unirse(self, ws: WebSocket, d: dict) -> Dispositivo:
        nombre = str(d.get("dispositivo", "")).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,19}", nombre):
            raise ValueError("Nombre inválido: usa letras, números y guiones (por ejemplo mic-1).")
        cliente_id = str(d.get("cliente_id", ""))[:64]
        disp = self.dispositivos.get(nombre)
        if disp and disp.ws and disp.cliente_id != cliente_id:
            raise ValueError(f"«{nombre}» ya está en uso por otro celular. Elige otro nombre.")
        if disp is None:
            disp = Dispositivo(nombre=nombre, cliente_id=cliente_id)
            self.dispositivos[nombre] = disp
        elif disp.ws and disp.ws is not ws:
            # El mismo celular con una conexión vieja que quedó colgada.
            try:
                await disp.ws.close()
            except Exception:
                pass
        disp.ws = ws
        disp.cliente_id = cliente_id
        disp.info = d.get("info") or {}
        disp.microfono = d.get("microfono") or {}
        disp.bateria = d.get("bateria")
        log.info("Celular conectado: %s (%s)", nombre, disp.info.get("modelo") or "modelo desconocido")

        await self.enviar(ws, {"tipo": "unido", "dispositivo": nombre, "sesion": self.info_sesion()})
        s = self.sesion
        tiene_tramo = d.get("tramo_actual") is not None
        if s and s.estado == "grabando" and not tiene_tramo:
            await self.enviar(ws, {"tipo": "iniciar", "motivo": "reconexion" if nombre in s.grabaciones else "inicio"})
        elif tiene_tramo and (s is None or s.estado != "grabando"):
            await self.enviar(ws, {"tipo": "terminar" if s is None or s.estado in ("terminando", "terminada") else "pausar"})
        return disp

    def desconectar(self, disp: Dispositivo | None, ws: WebSocket):
        if disp is None or disp.ws is not ws:
            return
        disp.ws = None
        log.info("Celular desconectado: %s", disp.nombre)
        if self.sesion is None or disp.nombre not in self.sesion.grabaciones:
            del self.dispositivos[disp.nombre]

    def abrir_tramo(self, disp: Dispositivo, d: dict):
        s = self.sesion
        if s is None or s.estado in ("preparada", "terminada"):
            return
        tid = int(d["tramo"])
        t_inicio = float(d["t_inicio_ms"])
        g = s.grabaciones.get(disp.nombre)
        if g is None:
            if s.carpeta is None:
                s.crear_carpeta()
            g = Grabacion(disp.nombre, s.carpeta / f"{disp.nombre}.wav", t_inicio, disp)
            s.grabaciones[disp.nombre] = g
            log.info("  %s empieza a grabar (hora servidor %s)", disp.nombre, iso(t_inicio))
        if tid in g.tramos:
            return  # reenviado tras reconectar: ya lo conocemos
        if not g.tramos:
            pos = 0
        else:
            # Se ubica por la hora; nunca encima de lo ya grabado.
            pos = max(round((t_inicio - g.inicio_ms) * FS / 1000), g.fin_esperado())
        g.tramos[tid] = Tramo(id=tid, motivo=str(d.get("motivo", "inicio")), t_inicio_ms=t_inicio, pos=pos)
        g.reloj = dict(disp.reloj)

    def cerrar_tramo(self, disp: Dispositivo, d: dict):
        s = self.sesion
        g = s.grabaciones.get(disp.nombre) if s else None
        t = g.tramos.get(int(d["tramo"])) if g else None
        if t is None:
            return
        t.muestras_fin = int(d["muestras"])
        t.t_fin_ms = float(d.get("t_fin_ms") or ahora_ms())
        g.bateria_fin = disp.bateria

    def audio(self, disp: Dispositivo, datos: bytes) -> dict | None:
        """Escribe un trozo. Devuelve un ack para el celular cuando corresponde."""
        s = self.sesion
        g = s.grabaciones.get(disp.nombre) if s else None
        if g is None or len(datos) < CABECERA_TROZO.size or g.escritor.f.closed:
            return None
        tid, seq, muestra = CABECERA_TROZO.unpack_from(datos)
        t = g.tramos.get(tid)
        if t is None:
            return None
        pcm = datos[CABECERA_TROZO.size:]
        pcm = pcm[: len(pcm) // 2 * 2]
        n = len(pcm) // 2
        pos = t.pos + muestra
        g.escritor.escribir(pos, pcm)
        g.cobertura.agregar(pos, pos + n)
        if self.en_vivo is not None and s.inicio_ms is not None:
            self.en_vivo.alimentar(disp.nombre, (g.inicio_ms - s.inicio_ms) / 1000, pos, pcm)
        g.ultimo_audio_ms = ahora_ms()
        t.recibido_hasta = max(t.recibido_hasta, muestra + n)

        if seq == t.seq_siguiente:
            t.seq_siguiente += 1
            while t.seq_siguiente in t.seq_adelantados:
                t.seq_adelantados.discard(t.seq_siguiente)
                t.seq_siguiente += 1
        elif seq > t.seq_siguiente:
            t.seq_adelantados.add(seq)
        if seq % ACK_CADA == 0:
            return {"tipo": "ack", "tramo": tid, "seq": t.seq_siguiente - 1}
        return None

    def deriva(self, disp: Dispositivo, d: dict):
        """Cada 10 s: cuántas muestras lleva el celular frente a la hora real."""
        s = self.sesion
        g = s.grabaciones.get(disp.nombre) if s else None
        t = g.tramos.get(int(d.get("tramo", -1))) if g else None
        if t is None or len(t.deriva) > 2000:
            return
        t.deriva.append([round((float(d["t_ms"]) - t.t_inicio_ms) / 1000, 3), int(d["muestra"]),
                         d.get("ctx_s"), d.get("saltados"), d.get("ritmo")])

    def marca_celular(self, disp: Dispositivo, d: dict):
        """El celular informa en qué muestra de su tramo estaba al recibir la marca."""
        s = self.sesion
        g = s.grabaciones.get(disp.nombre) if s else None
        n = d.get("n")
        if g is None or not isinstance(n, int) or not (1 <= n <= len(s.marcas)):
            return
        t = g.tramos.get(int(d.get("tramo", -1)))
        if t is not None:
            pos = s.marcas[n - 1]["posiciones"].setdefault(disp.nombre, {})
            muestra = t.pos + int(d["muestra"])
            pos["muestra_segun_celular"] = muestra
            pos["segundo_segun_celular"] = round(muestra / FS, 3)

    # ---------- panel ----------

    async def crear_sesion(self, curso: str, tema: str):
        if self.sesion and self.sesion.estado not in ("terminada",):
            raise ValueError("Ya hay una sesión abierta. Termínala primero.")
        curso = " ".join(curso.split())[:60]
        if not curso:
            raise ValueError("Escribe el nombre del curso.")
        self.sesion = Sesion(curso, " ".join(tema.split())[:120], self.raiz)
        # Olvida a los celulares desconectados de la sesión anterior.
        self.dispositivos = {n: d for n, d in self.dispositivos.items() if d.ws}
        log.info("Sesión creada: %s — %s", self.sesion.curso, self.sesion.tema)
        await self.a_celulares({"tipo": "sesion", "sesion": self.info_sesion()})

    async def iniciar(self):
        s = self._sesion("preparada", "pausada")
        reanudar = s.estado == "pausada"
        if not reanudar and not any(d.ws for d in self.dispositivos.values()):
            raise ValueError("No hay celulares conectados.")
        s.estado = "grabando"
        s.evento("reanudar" if reanudar else "inicio")
        if not reanudar and self.en_vivo is not None:
            self.en_vivo.nueva_sesion(s.inicio_ms)
        log.info("%s grabación", "Reanuda" if reanudar else "Inicia")
        for d in self.dispositivos.values():
            motivo = "reanudar" if reanudar and d.nombre in s.grabaciones else "inicio"
            await self.enviar(d.ws, {"tipo": "iniciar", "motivo": motivo})
        await self.a_celulares({"tipo": "sesion", "sesion": self.info_sesion()})

    async def pausar(self):
        s = self._sesion("grabando")
        s.estado = "pausada"
        s.evento("pausa")
        log.info("Pausa")
        await self.a_celulares({"tipo": "pausar"})
        await self.a_celulares({"tipo": "sesion", "sesion": self.info_sesion()})
        s.guardar_metadata()

    async def marca(self, etiqueta: str):
        s = self._sesion("grabando", "pausada")
        t = ahora_ms()
        posiciones = {}
        for n, g in s.grabaciones.items():
            tr = g.tramo_abierto()
            if tr is not None:
                muestra = tr.pos + round((t - tr.t_inicio_ms) * FS / 1000)
                posiciones[n] = {"muestra_segun_servidor": muestra, "segundo_segun_servidor": round(muestra / FS, 3)}
        numero = len(s.marcas) + 1
        s.marcas.append({"n": numero, "etiqueta": etiqueta[:60], "hora": iso(t), "t_ms": round(t, 1), "posiciones": posiciones})
        log.info("Marca %d (%s)", numero, etiqueta or "sin etiqueta")
        await self.a_celulares({"tipo": "marca", "n": numero})
        s.guardar_metadata()

    async def terminar(self):
        s = self._sesion("preparada", "grabando", "pausada")
        if s.estado == "preparada":
            s.estado = "terminada"
            return
        s.estado = "terminando"
        s.evento("fin")
        log.info("Terminando clase: esperando el audio pendiente de los celulares…")
        await self.a_celulares({"tipo": "terminar"})
        await self.a_celulares({"tipo": "sesion", "sesion": self.info_sesion()})

        # Espera a que cada celular conectado cierre su tramo (manda lo que le falte).
        limite = ahora_ms() + ESPERA_CIERRE_S * 1000
        while ahora_ms() < limite:
            faltan = [n for n, g in s.grabaciones.items()
                      if g.tramo_abierto() and self.dispositivos.get(n) and self.dispositivos[n].ws]
            if not faltan:
                break
            await asyncio.sleep(0.2)

        fin_ms = s.eventos[-1]["t_ms"]
        for n, g in s.grabaciones.items():
            g.escritor.extender(g.fin_esperado())
            g.escritor.cerrar()
            ultimo = max((t.t_fin_ms or 0 for t in g.tramos.values()), default=0) or fin_ms
            g.duracion_esperada_s = round((ultimo - g.inicio_ms) / 1000, 3)
            g.verificacion = audio.verificar_wav(g.escritor.ruta)
            g.verificacion["archivo"] = g.escritor.ruta.name
            pausas, huecos = g.pausas_y_huecos()
            g.verificacion["ceros_pct"] = audio.porcentaje_ceros(
                g.escritor.ruta, excluir=[(x["desde_muestra"], x["desde_muestra"] + x["muestras"]) for x in pausas + huecos])
        s.estado = "terminada"
        s.guardar_metadata()
        await self.a_celulares({"tipo": "sesion", "sesion": self.info_sesion()})
        if self.en_vivo is not None:
            clase = {"clase_id": s.carpeta.name if s.carpeta else "", "curso": s.curso, "tema": s.tema}
            await asyncio.to_thread(self.en_vivo.terminar, s.carpeta, clase)

        log.info("Clase guardada en %s", s.carpeta)
        for n, g in s.grabaciones.items():
            v = g.verificacion
            _, huecos = g.pausas_y_huecos()
            log.info("  %s %-10s %d Hz %d canal %s  real %.2f s  esperada %.2f s  huecos %.2f s  ceros %.1f %%",
                     "OK " if v["ok"] else "MAL", n, v["frecuencia_hz"], v["canales"], v["subtipo"],
                     v["duracion_s"], g.duracion_esperada_s, sum(h["duracion_s"] for h in huecos), v["ceros_pct"])

    def _sesion(self, *estados: str) -> Sesion:
        s = self.sesion
        if s is None:
            raise ValueError("No hay sesión. Crea una primero.")
        if s.estado not in estados:
            raise ValueError(f"No se puede hacer eso con la sesión {s.estado}.")
        return s

    def resumen_panel(self) -> dict:
        s = self.sesion
        ahora = ahora_ms()
        disp = []
        for n, d in sorted(self.dispositivos.items()):
            g = s.grabaciones.get(n) if s else None
            disp.append({
                "nombre": n,
                "conectado": d.ws is not None,
                "modelo": d.info.get("modelo"),
                "nivel_db": round(d.nivel_db, 1) if d.ws else None,
                "pico": d.pico,
                "bateria": d.bateria,
                "pendientes_s": d.pendientes_s,
                "ceros": round(d.ceros, 3) if d.ws else None,
                "grabando": bool(g and g.tramo_abierto()),
                "recibido_s": round(g.cobertura.total() / FS, 1) if g else 0,
                "sin_audio_s": round((ahora - g.ultimo_audio_ms) / 1000, 1) if g and g.ultimo_audio_ms and g.tramo_abierto() else None,
                "huecos_s": round(sum(h["duracion_s"] for h in g.pausas_y_huecos()[1]), 1) if g else 0,
            })
        sesion = None
        if s:
            grabando_s, total_s = s.segundos()
            sesion = {
                "curso": s.curso, "tema": s.tema, "estado": s.estado,
                "carpeta": str(s.carpeta.relative_to(self.raiz.parent.parent)) if s.carpeta else None,
                "grabando_s": round(grabando_s, 1), "total_s": round(total_s, 1),
                "marcas": [{"n": m["n"], "etiqueta": m["etiqueta"], "hora": m["hora"]} for m in s.marcas],
                "resultado": [
                    {"nombre": n, **g.verificacion, "esperada_s": g.duracion_esperada_s,
                     "huecos_s": round(sum(h["duracion_s"] for h in g.pausas_y_huecos()[1]), 2),
                     "pausas_s": round(sum(p["duracion_s"] for p in g.pausas_y_huecos()[0]), 2)}
                    for n, g in s.grabaciones.items() if g.verificacion
                ] if s.estado == "terminada" else None,
            }
        voz = self.en_vivo.resumen() if self.en_vivo is not None else None
        return {"tipo": "estado", "sesion": sesion, "dispositivos": disp, "voz": voz}

    # ---------- tareas de fondo ----------

    async def bucle(self):
        """Refresca el panel cada 0.5 s y guarda cabeceras/metadata cada 5 s."""
        vuelta = 0
        while True:
            await asyncio.sleep(0.5)
            vuelta += 1
            try:
                if self.paneles:
                    r = self.resumen_panel()
                    await asyncio.gather(*(self.enviar(p, r) for p in list(self.paneles)))
                s = self.sesion
                if vuelta % 10 == 0 and s and s.estado in ("grabando", "pausada"):
                    for g in s.grabaciones.values():
                        g.escritor.guardar()
                    s.guardar_metadata()
            except Exception:
                log.exception("Error en el bucle de fondo")
