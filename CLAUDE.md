# Aula que Escucha: contexto del proyecto

## Qué es
Sistema de percepción por sonido para un aula universitaria (unos 40 alumnos),
parte del proyecto "Aula Inteligente para Educación Universitaria" del curso
de Percepción Computacional (UPAO). Mi parte es el audio.

El sistema escucha la clase y produce tres cosas:
1. Asistencia: quién está presente, reconociendo su voz, sin pasar lista
   a mano.
2. Participación: quién habla, cuántas veces, cuánto tiempo y cuándo.
3. Transcripción: lo que se dijo en la clase, con quién lo dijo.

## Para quién es la salida
- El docente: un panel con asistencia y participación de cada clase.
- Otro grupo del curso (predicción): recibe la transcripción y las
  participaciones para predecir por curso y tema en qué temas los alumnos
  participan menos o tienen más dudas. La salida tiene que ser fácil de
  consumir para ellos.
- Posible integración con el grupo de video: ellos podrían mandar un conteo
  de personas por minuto para cruzarlo con la asistencia por voz (detectar
  alumnos que no hablaron o alguien que dice "presente" por otro). El video
  NO reconoce caras, solo cuenta.

## Restricciones
- Barato y realista: lo construyo yo. Por ahora uso celulares como
  micrófonos (página web que transmite a mi laptop por WiFi local o hotspot).
  Más adelante podrían ser ESP32 con INMP441, así que la captura no debe
  depender del tipo de dispositivo.
- Todo corre en mi laptop (Arch Linux), sin internet durante la clase.
  Nada de servicios en la nube.
- Audio estándar del proyecto: 16 kHz, mono, PCM 16 bits.
- Privacidad: las voces son datos personales (Ley 29733). Hay consentimiento
  en el registro, los datos quedan locales y no se suben a ningún lado.

## Requisitos no funcionales
- Reconocer al menos el 70 % de las voces correctamente.
- Marcar asistencia en menos de 10 s desde que el alumno habla.
- Aguantar 2 horas de clase sin caerse.
- Que el docente aprenda a usarlo en menos de 15 minutos.

## Pipeline previsto
1. Captura: modo registro (huella de voz de cada alumno) y modo clase
   (varios celulares grabando la clase entera). YA EXISTE.
2. Detección de voz: Silero VAD para separar voz de silencio y ruido.
3. Identificación de hablante: SpeechBrain ECAPA. Cada alumno tiene una huella
   (promedio de embeddings de su registro). Cada segmento de voz se compara
   contra las huellas; si la similitud no pasa un umbral, queda como
   "desconocido". Las huellas se actualizan con segmentos de alta confianza
   para que el sistema mejore con las clases.
4. Asistencia: un alumno está presente si se le reconoce en la ventana de
   inicio de clase (cuando el docente pasa lista). Por ahora, sin transcripción,
   toda la sesión cuenta como ventana de asistencia; más adelante la
   transcripción detectará cuándo el docente pasa lista.
5. Participación: agregación de segmentos por alumno (intervenciones,
   tiempo total, momentos).
6. Transcripción: faster-whisper en español, después de la clase (no en vivo).
   Se alinea con los segmentos de hablante para saber quién dijo qué.
7. Salida y panel del docente.

## Contrato de salida (para el grupo de predicción)
Por cada clase, un CSV o JSON con una fila por intervención:
clase_id, curso, tema, inicio_s, fin_s, hablante, texto
Más un resumen de asistencia por alumno. No cambies este formato sin avisarme,
porque otro grupo depende de él.

## Cómo trabajar conmigo
- Avanza por partes y déjame probar cada una antes de seguir.
- Cada etapa tiene que poder ejecutarse sola sobre los audios guardados,
  para poder repetir el procesamiento sin volver a grabar.
- Guarda métricas (precisión, confusiones, tiempos) porque van a mi informe.
- Pregúntame antes de decisiones importantes que no estén aquí.
- Explícame en español y de forma directa.
- Git: commits convencionales en español, sin `Co-Authored-By` ni ninguna
  mención a Claude (el repo sale solo a mi nombre). Nunca subir `data/`,
  `Audios y cosas S4/`, certificados ni modelos.
