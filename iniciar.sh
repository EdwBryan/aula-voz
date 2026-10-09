#!/usr/bin/env bash
# Levanta todo: entorno de Python, certificado (si falta) y servidor HTTPS en el puerto 8000.
set -euo pipefail
cd "$(dirname "$0")"

if ! .venv/bin/python -c "import fastapi, speechbrain, silero_vad, faster_whisper" 2>/dev/null; then
    echo "Instalando el entorno de Python (necesita internet solo esta vez; torch pesa ~200 MB)..."
    if command -v uv >/dev/null; then
        [ -x .venv/bin/python ] || uv venv -q -p 3.12 .venv
        uv pip install -q -p .venv -r requirements.txt --index-strategy unsafe-best-match
    else
        [ -x .venv/bin/python ] || python3 -m venv .venv
        .venv/bin/pip install -q -r requirements.txt
    fi
fi

[ -f certs/certificado.pem ] || ./scripts/generar_certificado.sh

# Modelos (ECAPA ~90 MB y Whisper small ~460 MB): se descargan una sola vez, antes de prender el hotspot.
if [ ! -f modelos/ecapa/hyperparams.yaml ] || [ ! -d modelos/whisper ]; then
    echo "Descargando modelos de voz (necesita internet solo esta vez)..."
    .venv/bin/python -m reconocimiento.modelos
fi

# Avisa si una IP actual no está en el certificado (p. ej. cambió la IP de casa).
san=$(openssl x509 -in certs/certificado.pem -noout -ext subjectAltName)
for ip in $(ip -4 -o addr show scope global | awk '{split($4, a, "/"); print a[1]}'); do
    grep -q "IP Address:$ip\b" <<<"$san" || echo "AVISO: $ip no está en el certificado. Ejecuta ./scripts/generar_certificado.sh"
done

echo
echo "Abre en el celular:"
for ip in $(ip -4 -o addr show scope global | awk '{split($4, a, "/"); print a[1]}'); do
    echo "   https://$ip:8000"
done
echo

exec .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000 \
    --ssl-keyfile certs/clave.pem --ssl-certfile certs/certificado.pem
