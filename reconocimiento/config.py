"""Parámetros del reconocimiento. Lo que no es valor por defecto de una herramienta
está marcado como elección propia."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data"
REGISTRO = DATOS / "registro"
CLASES = DATOS / "clase"
HUELLAS = DATOS / "huellas"
METRICAS = DATOS / "metricas"
MODELOS = RAIZ / "modelos"

FS = 16000  # Silero VAD y ECAPA están entrenados a 16 kHz

# Silero VAD: valores por defecto de la herramienta
SILERO = dict(threshold=0.5, min_speech_duration_ms=250, min_silence_duration_ms=100, speech_pad_ms=30)

MODELO_ECAPA = "speechbrain/spkrec-ecapa-voxceleb"

# Elección propia: con menos de 0.5 s de voz la huella ECAPA es poco fiable.
SEGMENTO_MIN_S = 0.5
# Elección propia: un turno largo se corta en trozos de 6 s. Así en vivo se identifica
# a quien habla sin esperar a que termine (requisito: asistencia en < 10 s).
SEGMENTO_MAX_S = 6.0

# Umbral de similitud coseno para aceptar a un alumno (si no, "desconocido").
# Se calibra con el registro, pero el registro se graba de cerca y en clase la voz llega de
# lejos: la similitud con la propia huella baja de ~0.7 a 0.2-0.6. Por eso el umbral
# calibrado no puede pasar de UMBRAL_MAXIMO (elección propia, medida con
# reconocimiento.evaluar: con 0.30 no se aceptó a ninguna persona equivocada).
UMBRAL_POR_DEFECTO = 0.30
UMBRAL_MAXIMO = 0.30
# Elección propia: para actualizar una huella el segmento debe superar el umbral por este
# margen y ganarle al segundo candidato por al menos MARGEN_SEGUNDO.
MARGEN_ACTUALIZAR = 0.15
MARGEN_SEGUNDO = 0.10

# Ventana de asistencia en segundos desde el inicio de la clase (None = toda la clase).
# Por ahora toda la clase cuenta: sin transcripción no se sabe cuándo el docente pasa lista.
VENTANA_ASISTENCIA_S = None
