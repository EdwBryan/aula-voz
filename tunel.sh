#!/usr/bin/env bash
# Publica SOLO el modo registro en internet con un túnel temporal de Cloudflare
# (sin cuenta). Tus compañeros abren el enlace con sus datos o el WiFi de la universidad.
# El panel y el modo clase quedan bloqueados por el túnel. Ctrl+C lo cierra.
set -euo pipefail
cd "$(dirname "$0")"
command -v cloudflared >/dev/null || { echo "Falta cloudflared. Instálalo con:  sudo pacman -S cloudflared"; exit 1; }

registro=$(mktemp)
servidor=""
tunel=""
trap 'kill $tunel $servidor 2>/dev/null; rm -f "$registro"' EXIT

# Levanta el servidor si no está corriendo ya.
if ! curl -sk -o /dev/null https://localhost:8000/; then
    echo "Levantando el servidor..."
    ./iniciar.sh >/dev/null 2>&1 &
    servidor=$!
    until curl -sk -o /dev/null https://localhost:8000/; do
        kill -0 $servidor 2>/dev/null || { echo "El servidor no arrancó. Prueba ./iniciar.sh para ver el error."; exit 1; }
        sleep 0.5
    done
fi

echo "Abriendo el túnel..."
# --edge-ip-version 4: algunas redes (la de la universidad) bloquean IPv6 hacia Cloudflare.
cloudflared tunnel --no-autoupdate --edge-ip-version 4 --url https://localhost:8000 --no-tls-verify >"$registro" 2>&1 &
tunel=$!
url=""
for _ in $(seq 60); do
    # El enlace real; api.trycloudflare.com aparece solo en los mensajes de error.
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$registro" | grep -v '://api\.' | head -1 || true)
    [ -n "$url" ] && break
    kill -0 $tunel 2>/dev/null || break
    sleep 0.5
done
if [ -z "$url" ]; then
    echo "No se pudo abrir el túnel. Mensaje de cloudflared:"
    grep -i "fail\|error\|ERR" "$registro" | tail -3
    echo "Suele ser la red (bloquea Cloudflare). Prueba con otra red o con los datos del celular."
    exit 1
fi

echo
echo "  Enlace para registrarse:  $url/registro"
echo
qrencode -t ANSIUTF8 "$url/registro" 2>/dev/null || true
echo "El enlace cambia cada vez que abres el túnel. Ctrl+C para cerrarlo."
wait $tunel
