# aula-voz · Aula que Escucha

Sistema de percepción por sonido para un aula universitaria: escucha la clase con
celulares como micrófonos y produce **asistencia** (quién está, reconociendo su voz),
**participación** (quién habla, cuántas veces y cuándo) y, más adelante, la
**transcripción** de lo que dijo cada uno.

Es la parte de audio del proyecto *Aula Inteligente para Educación Universitaria*
del curso de Percepción Computacional (UPAO). Todo corre en una laptop, sin internet
durante la clase y sin servicios en la nube.

| Etapa | Herramienta | Estado |
|---|---|---|
| 1. Captura (registro y clase) | Página web en el celular → WebSocket → WAV | ✅ |
| 2. Detección de voz | Silero VAD | ✅ |
| 3. Identificación de hablante | SpeechBrain ECAPA + huellas de voz | ✅ |
| 4. Asistencia | Reconocimiento en vivo y por lotes | ✅ |
| 5. Participación | Intervenciones y tiempo de voz por alumno | 🟡 básico (en la asistencia) |
| 6. Transcripción | faster-whisper (después de la clase) | ⏳ pendiente |
| 7. Panel del docente y salida para el grupo de predicción | | 🟡 panel de asistencia |

Audio estándar del proyecto: **WAV, 16 kHz, mono, PCM 16 bits**.

---

## Instalación

Requisitos: Linux (probado en Arch), Python 3.12 y `openssl`. Opcionales: `uv`
(instala más rápido), `cloudflared` (túnel), `qrencode` (QR en la terminal).

```bash
git clone https://github.com/EdwBryan/aula-voz.git
cd aula-voz
./iniciar.sh
```

La primera vez, `iniciar.sh` (con internet):

1. crea `.venv/` e instala `requirements.txt` (torch en versión CPU, unos 200 MB),
2. genera un certificado autofirmado en `certs/` (el micrófono del navegador exige HTTPS),
3. descarga el modelo ECAPA a `modelos/` (unos 90 MB).

Después funciona sin internet. Muestra las direcciones para abrir en el celular,
por ejemplo `https://10.42.0.1:8000` (hotspot) o `https://192.168.0.11:8000` (WiFi de casa).
Si cambia la IP de tu red, `iniciar.sh` avisa: regenera con `./scripts/generar_certificado.sh`.

### Aceptar el certificado en el celular

El navegador muestra una advertencia **una vez por cada dirección**:

- **Chrome (Android):** «Tu conexión no es privada» → **Configuración avanzada** → **Acceder a … (sitio no seguro)**.
- **Safari (iPhone):** **Mostrar detalles** → **visitar este sitio web** → **Visitar sitio web**.
- **Firefox (Android):** **Avanzado** → **Aceptar el riesgo y continuar**.

Luego, al grabar, **Permitir** el micrófono.

---

## Uso en 4 pasos

### 1. Registro de voces (una vez por alumno)

Cada alumno abre `/registro` en su celular: nombre, código, consentimiento y 3 tomas
de hasta 5 s leyendo una frase distinta. Se rechazan tomas cortas, silenciosas o saturadas.

```
data/registro/<codigo>_<Nombre>/toma_1.wav … toma_3.wav, metadata.json
```

Para registrar a compañeros que no están en tu red: `./tunel.sh` (ver [Túnel](#túnel-por-internet)).

### 2. Crear las huellas de voz

```bash
.venv/bin/python -m reconocimiento.huellas
```

Cada huella es el promedio de los embeddings ECAPA (192 números) de las tomas del alumno.
También calibra el umbral de «desconocido» y guarda la calibración en `data/metricas/`.
Vuelve a ejecutarlo cada vez que se registre alguien nuevo.

### 3. Clase en vivo

1. En la laptop: **https://localhost:8000/control** (el panel solo abre desde la laptop).
   Crea la sesión con curso y tema.
2. En cada celular: `https://10.42.0.1:8000/clase`, elige un nombre (mic-1, mic-2…) y **Unirme**.
3. En el panel: **Iniciar**, **Pausar/Reanudar**, **Marca** y **Terminar clase**.

Mientras se graba, la sección **Asistencia por voz** del panel marca a cada alumno como
presente en cuanto se le reconoce (en las pruebas tarda ~0.1 s desde que termina de hablar)
y muestra las últimas voces con su similitud. Por ahora **toda la clase cuenta para la
asistencia**: cuando exista la transcripción se detectará el momento en que el docente pasa lista.

```
data/clase/<AAAA-MM-DD_HH-MM>_<Curso>/
  mic-1.wav, mic-2.wav…          audio completo de cada celular
  metadata.json                  sesión, pausas, huecos, marcas
  en_vivo.json                   cada segmento reconocido en vivo, con su latencia
  asistencia_en_vivo.json/.csv   asistencia calculada en vivo
```

Pausas y huecos quedan en **silencio** dentro del WAV: el segundo N del archivo es el
segundo N desde el inicio de ese celular.

### 4. Después de la clase (opcional)

Cada etapa se ejecuta sola sobre los audios guardados, así que se puede repetir el
procesamiento sin volver a grabar (sin argumento usa la clase más reciente):

```bash
.venv/bin/python -m reconocimiento.vad          data/clase/<carpeta>   # → segmentos.json
.venv/bin/python -m reconocimiento.identificar  data/clase/<carpeta>   # → identificacion.json
.venv/bin/python -m reconocimiento.asistencia   data/clase/<carpeta>   # → asistencia.json/.csv
.venv/bin/python -m reconocimiento.procesar     data/clase/<carpeta>   # las tres seguidas

.venv/bin/python -m reconocimiento.actualizar_huellas data/clase/<carpeta>
```

`actualizar_huellas` añade a cada huella los segmentos muy seguros de esa clase, para que el
sistema aprenda cómo suena cada alumno de lejos. Cada clase se aplica una vez y la huella
anterior queda en `data/huellas/historial/`.

---

## Salidas

**`asistencia.csv`** (una fila por alumno registrado):

| columna | significado |
|---|---|
| `clase_id`, `curso`, `tema` | identificación de la clase |
| `codigo`, `nombre` | alumno |
| `presente` | `True` si se le reconoció en la ventana de asistencia |
| `primera_vez_s` | segundo de la clase en que se le oyó por primera vez |
| `intervenciones` | turnos de voz (más de 2 s de silencio separan dos) |
| `voz_s` | segundos de voz atribuidos |
| `mejor_similitud` | similitud coseno más alta con su huella |
| `microfonos` | celulares que lo oyeron |

**`identificacion.json`**: una entrada por segmento de voz con `mic`, `inicio_s`/`fin_s`
(dentro del WAV), `inicio_clase_s`/`fin_clase_s` (desde el inicio de la clase), `codigo`
(`null` = desconocido), `similitud`, `candidato` (el más parecido) y `segundo` (similitud del
segundo más parecido).

Pendiente para el grupo de predicción (cuando esté la transcripción): un archivo por clase con
una fila por intervención: `clase_id, curso, tema, inicio_s, fin_s, hablante, texto`.

---

## Evaluación y métricas

```bash
.venv/bin/python -m reconocimiento.evaluar
```

Usa audios anotados (`data/evaluacion/referencia.json`, local: son voces y no se suben) y
mide **A)** la identificación sobre los tramos anotados y **B)** el sistema completo
(VAD + ECAPA), con matriz de confusión y tiempo por segmento → `data/metricas/`.

Resultados del 2026-10-08 (2 personas, umbral 0.30):

| Prueba | Identificación sola | Completo (VAD + ECAPA) | Sin umbral |
|---|---|---|---|
| Intercalado (35 «presente») | 97.1 % | 85.7 % | 100 % |
| Con ruido, parte confiable (13) | 69.2 % | 92.3 % | 100 % |

- ECAPA casi no confunde a una persona con otra; los errores son sobre todo «desconocido».
- El registro se graba de cerca y en clase la voz llega de lejos: la similitud con la propia
  huella baja de ~0.7 a 0.2–0.6. Por eso el umbral calibrado no pasa de 0.30
  (`UMBRAL_MAXIMO` en `reconocimiento/config.py`).
- En vivo, identificar un segmento tarda ~0.1 s en CPU (requisito: menos de 10 s).

---

## Túnel por internet

```bash
./tunel.sh             # publica solo /registro
./tunel.sh --pruebas   # SOLO PRUEBAS: también /clase y /prueba-mic
```

Abre un túnel temporal de Cloudflare (sin cuenta, `sudo pacman -S cloudflared`) y muestra un
enlace `https://<algo>.trycloudflare.com` con su QR. El panel `/control` nunca se publica.
El enlace cambia cada vez y Ctrl+C lo cierra. No funciona con el hotspot prendido (la laptop
necesita internet). El registro del servidor queda en `data/servidor.log`.

`--pruebas` sirve para que un celular con datos móviles mande audio de clase mientras la
laptop sigue conectada a internet. **No usarlo en una clase real**: las voces no deben salir
de la red local.

---

## Hotspot de la laptop

`hotspot-programa/` crea una red WiFi propia (SSID *AulaQueEscucha*) para que los celulares
se conecten a la laptop sin router. `hotspot on|off|run`, o la ventana `hotspot-ventana`
(GTK4). En el celular hay que **apagar los datos móviles** (si no, Android manda el tráfico por ahí).
Con el hotspot prendido la laptop no tiene internet.

---

## Comprobar los archivos

```bash
.venv/bin/python -m server.verificar     # revisa que todos los .wav de data/ sean 16 kHz mono PCM 16
```

`/prueba-mic` detecta si el celular aplica una compuerta de ruido (audio en cero exacto):
con ella el reconocimiento no funciona.

---

## Estructura

```
server/          servidor FastAPI
  main.py          rutas, WebSockets, túnel
  clase.py         sesiones de clase y WAV continuo
  en_vivo.py       reconocimiento de voz en vivo (hilo aparte)
  audio.py         validar/guardar/verificar WAV
reconocimiento/  pipeline por etapas (cada una: python -m reconocimiento.<etapa>)
  config.py        parámetros y su origen
  modelos.py       Silero VAD y ECAPA
  audio.py         lectura y segmentación
  huellas.py, vad.py, identificar.py, asistencia.py, actualizar_huellas.py, evaluar.py
static/          páginas del celular y del panel
hotspot-programa/ hotspot de la laptop
scripts/         generar_certificado.sh
data/            grabaciones, huellas y métricas (fuera de git)
modelos/         modelo ECAPA descargado (fuera de git)
```

## Privacidad

Las voces son datos personales (Ley N.° 29733). Cada alumno acepta un consentimiento al
registrarse. Las grabaciones, huellas y métricas quedan en `data/` dentro de la laptop y
**nunca se suben** (están en `.gitignore`). Para borrar a un alumno: elimina su carpeta en
`data/registro/` y vuelve a ejecutar `python -m reconocimiento.huellas`.
