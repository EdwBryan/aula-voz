"""Servidor de "Aula que Escucha": sirve la página y recibe audio por WebSocket."""

import asyncio
import json
import logging
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import audio
from .clase import EstadoClase
from .util import ahora_ms, iso, limpiar_nombre

RAIZ = Path(__file__).resolve().parent.parent
ESTATICOS = RAIZ / "static"
DATOS = RAIZ / "data"
REGISTRO = DATOS / "registro"
REGISTRO_ANTERIORES = DATOS / "registro_anteriores"

# Una frase distinta por toma (más variedad de sonidos para el modelo de voz).
# Cada una dura unos 2-4 s leída: con menos voz que eso el reconocimiento empeora.
FRASES = [
    "Presente, soy {nombre}, de ingeniería de sistemas.",
    "Profe, tengo una pregunta sobre el tema de hoy.",
    "Disculpe, ¿podría repetir la última parte, por favor?",
]
TOMAS = len(FRASES)
CONSENTIMIENTO = (
    "Acepto que se grabe mi voz y que estas grabaciones se usen solo dentro del "
    "proyecto «Aula que Escucha» (registro de asistencia y participación por voz). "
    "Puedo pedir que se borren en cualquier momento."
)
MAX_BYTES_TOMA = int(audio.DURACION_MAX_S * audio.FS * 2) + 64_000

log = logging.getLogger("aula")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

clase = EstadoClase(DATOS / "clase")


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    tarea = asyncio.create_task(clase.bucle())
    yield
    tarea.cancel()


app = FastAPI(title="Aula que Escucha", lifespan=ciclo_de_vida)

# ---------- túnel (cloudflared) ----------
# Por el túnel solo se publica el modo registro. Todo lo que llega por el túnel trae
# estas cabeceras (y aparece como si viniera de 127.0.0.1, así que no basta con la IP).
CABECERAS_TUNEL = {b"cf-connecting-ip", b"cf-ray", b"x-forwarded-for"}
RUTAS_TUNEL = {"/registro", "/api/registro/config", "/ws/registro", "/favicon.ico"}
PREFIJOS_TUNEL = ("/static/css/", "/static/js/captura.js", "/static/js/registro.js", "/static/js/pcm-worklet.js")


def por_tunel(scope) -> bool:
    return any(k in CABECERAS_TUNEL for k, _ in scope.get("headers", []))


class SoloRegistroPorTunel:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and por_tunel(scope):
            ruta = scope["path"]
            if scope["type"] == "http" and ruta == "/":
                await send({"type": "http.response.start", "status": 307,
                            "headers": [(b"location", b"/registro"), (b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
            if ruta not in RUTAS_TUNEL and not ruta.startswith(PREFIJOS_TUNEL):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                else:
                    await send({"type": "http.response.start", "status": 404,
                                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await send({"type": "http.response.body", "body": "No disponible.".encode()})
                return
        await self.app(scope, receive, send)


app.add_middleware(SoloRegistroPorTunel)
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.get("/")
def inicio():
    return FileResponse(ESTATICOS / "index.html")


@app.get("/registro")
def pagina_registro():
    return FileResponse(ESTATICOS / "registro.html")


@app.get("/api/registro/config")
def config_registro():
    return {
        "tomas": TOMAS,
        "frases": FRASES,
        "consentimiento": CONSENTIMIENTO,
        "duracion_min_s": audio.DURACION_MIN_S,
        "duracion_max_s": audio.DURACION_MAX_S,
    }


def validar_alumno(datos: dict) -> tuple[str, str]:
    nombre = " ".join(str(datos.get("nombre", "")).split())
    codigo = str(datos.get("codigo", "")).strip()
    if not (2 <= len(nombre) <= 80) or not limpiar_nombre(nombre):
        raise ValueError("Escribe tu nombre completo.")
    if not re.fullmatch(r"[A-Za-z0-9-]{3,20}", codigo):
        raise ValueError("El código solo puede tener letras, números o guiones (3 a 20).")
    if datos.get("consentimiento") is not True:
        raise ValueError("Falta aceptar el consentimiento.")
    return nombre, codigo


def archivar_registros_previos(codigo: str) -> list[str]:
    """Si el alumno ya se había registrado, mueve lo anterior a data/registro_anteriores/."""
    movidos = []
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    for previa in REGISTRO.glob(f"{codigo}_*"):
        if previa.is_dir():
            destino = REGISTRO_ANTERIORES / f"{previa.name}__{marca}"
            REGISTRO_ANTERIORES.mkdir(parents=True, exist_ok=True)
            shutil.move(previa, destino)
            movidos.append(str(destino.relative_to(RAIZ)))
    return movidos


@app.websocket("/ws/registro")
async def ws_registro(ws: WebSocket):
    """Protocolo (JSON en texto, audio Int16 little-endian en binario):

    → {"tipo":"inicio", nombre, codigo, consentimiento, dispositivo, microfono}
    ← {"tipo":"listo"}
    por cada toma n:
      → {"tipo":"toma", "numero":n}   → binario...   → {"tipo":"fin_toma", "numero":n}
      ← {"tipo":"toma", "numero":n, "ok":bool, "motivo", medidas...}
    → {"tipo":"terminar"}
    ← {"tipo":"guardado", "carpeta", "verificacion":[...]}   o   {"tipo":"error", "mensaje"}
    """
    await ws.accept()
    cliente = ws.client.host if ws.client else None
    tmp = REGISTRO / f".tmp-{uuid.uuid4().hex[:8]}"
    alumno = None
    tomas: dict[int, dict] = {}
    actual: int | None = None
    buf = bytearray()

    async def error(mensaje: str):
        await ws.send_json({"tipo": "error", "mensaje": mensaje})

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            if msg.get("bytes") is not None:
                if actual is None:
                    await error("Llegó audio fuera de una toma.")
                    continue
                buf.extend(msg["bytes"])
                if len(buf) > MAX_BYTES_TOMA:
                    await error("La toma es demasiado larga.")
                    await ws.close()
                    break
                continue

            datos = json.loads(msg.get("text") or "{}")
            tipo = datos.get("tipo")

            if tipo == "inicio":
                try:
                    nombre, codigo = validar_alumno(datos)
                except ValueError as e:
                    await error(str(e))
                    continue
                alumno = {
                    "nombre": nombre,
                    "codigo": codigo,
                    "dispositivo": datos.get("dispositivo") or {},
                    "microfono": datos.get("microfono") or {},
                }
                await ws.send_json({"tipo": "listo"})

            elif tipo == "toma":
                n = datos.get("numero")
                if alumno is None or n not in range(1, TOMAS + 1):
                    await error("Toma inválida.")
                    continue
                actual, buf = n, bytearray()

            elif tipo == "fin_toma":
                if actual is None or datos.get("numero") != actual:
                    await error("Fin de toma inesperado.")
                    continue
                pcm = audio.pcm_desde_bytes(bytes(buf))
                medidas = audio.evaluar_toma(pcm)
                if medidas["ok"]:
                    archivo = tmp / f"toma_{actual}.wav"
                    audio.guardar_wav(archivo, pcm)
                    tomas[actual] = {"archivo": archivo.name, "frase": FRASES[actual - 1].format(nombre=alumno["nombre"]), **medidas}
                await ws.send_json({"tipo": "toma", "numero": actual, **medidas})
                actual, buf = None, bytearray()

            elif tipo == "terminar":
                if alumno is None or sorted(tomas) != list(range(1, TOMAS + 1)):
                    faltan = [n for n in range(1, TOMAS + 1) if n not in tomas]
                    await error(f"Faltan tomas válidas: {faltan}.")
                    continue
                carpeta = REGISTRO / f"{alumno['codigo']}_{limpiar_nombre(alumno['nombre'])}"
                metadata = {
                    "nombre": alumno["nombre"],
                    "codigo": alumno["codigo"],
                    "fecha": iso(),
                    "consentimiento": {"aceptado": True, "texto": CONSENTIMIENTO},
                    "dispositivo": {**alumno["dispositivo"], "ip": cliente},
                    "microfono": alumno["microfono"],
                    "audio": {"frecuencia_hz": audio.FS, "canales": audio.CANALES, "formato": "WAV", "subtipo": audio.SUBTIPO},
                    "tomas": [tomas[n] for n in sorted(tomas)],
                }
                (tmp / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

                anteriores = archivar_registros_previos(alumno["codigo"])
                tmp.rename(carpeta)
                verificacion = [audio.verificar_wav(carpeta / t["archivo"]) for t in metadata["tomas"]]

                log.info("Registro guardado: %s (%s)", carpeta.relative_to(RAIZ), metadata["dispositivo"].get("modelo"))
                for v in verificacion:
                    log.info("  %s %s  %d Hz  %d canal  %s  %.2f s",
                             "OK " if v["ok"] else "MAL", Path(v["archivo"]).name,
                             v["frecuencia_hz"], v["canales"], v["subtipo"], v["duracion_s"])
                for a in anteriores:
                    log.info("  registro anterior movido a %s", a)

                await ws.send_json({
                    "tipo": "guardado",
                    "carpeta": str(carpeta.relative_to(RAIZ)),
                    "verificacion": [{**v, "archivo": Path(v["archivo"]).name} for v in verificacion],
                    "reemplazo_anterior": bool(anteriores),
                })
                await ws.close()
                break

            else:
                await error(f"Mensaje desconocido: {tipo!r}")
    except WebSocketDisconnect:
        pass
    finally:
        # Un registro a medias no deja basura.
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


# ======================= Modo clase =======================


def es_la_laptop(scope) -> bool:
    """La conexión viene de la propia laptop (localhost o una IP propia)."""
    cliente = (scope.get("client") or ("",))[0]
    servidor = (scope.get("server") or ("",))[0]
    if por_tunel(scope):
        return False
    return cliente in ("127.0.0.1", "::1") or cliente == servidor


@app.get("/prueba-mic")
def pagina_prueba_mic():
    return FileResponse(ESTATICOS / "prueba-mic.html")


@app.post("/api/prueba-mic")
async def guardar_prueba_mic(request: Request):
    """Guarda la serie por segundo de la página de diagnóstico en data/pruebas/."""
    cuerpo = await request.body()
    if len(cuerpo) > 2_000_000:
        return PlainTextResponse("Demasiado grande.", status_code=413)
    datos = json.loads(cuerpo)
    datos["fecha"] = iso()
    datos["ip"] = request.client.host if request.client else None
    carpeta = DATOS / "pruebas"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / f"{datetime.now():%Y-%m-%d_%H-%M-%S}_prueba-mic.json"
    archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    seg = datos.get("segundos") or []
    con_filtro = sum(1 for x in seg if x.get("ceros", 0) > 0.3)
    log.info("Prueba de micrófono guardada: %s (%d s, %d s con filtro, primera vez: %s s)",
             archivo.name, len(seg), con_filtro, datos.get("primera_compuerta_s"))
    return {"archivo": archivo.name}


@app.get("/clase")
def pagina_clase():
    return FileResponse(ESTATICOS / "clase.html")


@app.get("/control")
def pagina_control(request: Request):
    if not es_la_laptop(request.scope):
        return PlainTextResponse("El panel de control solo se abre desde la laptop.", status_code=403)
    return FileResponse(ESTATICOS / "control.html")


@app.websocket("/ws/clase")
async def ws_clase(ws: WebSocket):
    """Celular del aula. JSON en texto; audio en binario:
    cabecera de 12 bytes (tramo, seq, muestra: uint32 LE) + Int16 LE a 16 kHz mono."""
    await ws.accept()
    disp = None
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if disp is not None:
                    ack = clase.audio(disp, msg["bytes"])
                    if ack:
                        await ws.send_json(ack)
                continue

            d = json.loads(msg.get("text") or "{}")
            tipo = d.get("tipo")
            if tipo == "ping":
                await ws.send_json({"tipo": "pong", "t_cliente": d.get("t"), "t_servidor": ahora_ms()})
            elif tipo == "unirse":
                try:
                    disp = await clase.unirse(ws, d)
                except ValueError as e:
                    await ws.send_json({"tipo": "error", "mensaje": str(e)})
            elif disp is None:
                await ws.send_json({"tipo": "error", "mensaje": "Primero hay que unirse."})
            elif tipo == "nivel":
                disp.nivel_db = float(d.get("db", -120))
                disp.pico = float(d.get("pico", 0))
                disp.pendientes_s = float(d.get("pendientes_s", 0))
                disp.ceros = float(d.get("ceros", 0))
            elif tipo == "bateria":
                disp.bateria = d.get("bateria")
            elif tipo == "reloj":
                disp.reloj = {"desfase_ms": d.get("desfase_ms"), "rtt_ms": d.get("rtt_ms")}
            elif tipo == "tramo":
                clase.abrir_tramo(disp, d)
            elif tipo == "fin_tramo":
                clase.cerrar_tramo(disp, d)
                await ws.send_json({"tipo": "ack", "tramo": d["tramo"], "seq": int(d.get("seq_final", -1))})
            elif tipo == "deriva":
                clase.deriva(disp, d)
            elif tipo == "marca_ok":
                clase.marca_celular(disp, d)
    except WebSocketDisconnect:
        pass
    finally:
        clase.desconectar(disp, ws)


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket):
    if not es_la_laptop(ws.scope):
        await ws.close(code=4403)
        return
    await ws.accept()
    clase.paneles.add(ws)
    try:
        await ws.send_json(clase.resumen_panel())
        while True:
            d = await ws.receive_json()
            orden = d.get("orden")
            try:
                if orden == "crear":
                    await clase.crear_sesion(str(d.get("curso", "")), str(d.get("tema", "")))
                elif orden == "iniciar":
                    await clase.iniciar()
                elif orden == "pausar":
                    await clase.pausar()
                elif orden == "marca":
                    await clase.marca(str(d.get("etiqueta", "")))
                elif orden == "terminar":
                    await clase.terminar()
                else:
                    raise ValueError(f"Orden desconocida: {orden!r}")
            except ValueError as e:
                await ws.send_json({"tipo": "error", "mensaje": str(e)})
            await ws.send_json(clase.resumen_panel())
    except WebSocketDisconnect:
        pass
    finally:
        clase.paneles.discard(ws)
