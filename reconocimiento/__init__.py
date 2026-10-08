"""Reconocimiento de hablante: Silero VAD → ECAPA (huellas) → asistencia.

Cada etapa se ejecuta sola sobre los audios guardados (python -m reconocimiento.<etapa>)
y el servidor usa las mismas funciones en vivo (server/en_vivo.py).
"""
