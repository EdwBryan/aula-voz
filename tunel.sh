#!/usr/bin/env bash
# Publica SOLO el modo registro en internet con un túnel temporal de Cloudflare
# (sin cuenta). Tus compañeros abren el enlace con sus datos o el WiFi de la universidad.
# El panel y el modo clase quedan bloqueados por el túnel. Ctrl+C lo cierra.
#
#   ./tunel.sh --pruebas   SOLO PARA PRUEBAS: publica también /clase y /prueba-mic, para que
#                          un celular con datos móviles mande audio mientras la laptop sigue
#                          con internet. El panel /control sigue solo en la laptop.
set -euo pipefail
cd "$(dirname "$0")"

pagina=registro
if [ "${1:-}" = "--pruebas" ]; then
    export AULA_TUNEL_PRUEBAS=1
    pagina=clase
fi
command -v cloudflared >/dev/null || { echo "Falta cloudflared. Instálalo con:  sudo pacman -S cloudflared"; exit 1; }

registro=$(mktemp)
servidor=""
tunel=""
trap 'kill $tunel $servidor 2>/dev/null; rm -f "$registro"' EXIT

# Levanta el servidor si no está corriendo ya.
if [ -n "${AULA_TUNEL_PRUEBAS:-}" ] && curl -sk -o /dev/null https://localhost:8000/; then
    echo "El servidor ya está corriendo sin el modo pruebas. Ciérralo (Ctrl+C en ./iniciar.sh) y vuelve a correr ./tunel.sh --pruebas"
    exit 1
fi
if ! curl -sk -o /dev/null https://localhost:8000/; then
    echo "Levantando el servidor..."
    mkdir -p data
    ./iniciar.sh >data/servidor.log 2>&1 &
    servidor=$!
    echo "Registro del servidor: data/servidor.log  (tail -f data/servidor.log)"
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
if [ "$pagina" = clase ]; then
    echo "  MODO PRUEBAS (no usar en una clase real)"
    echo "  Celular, modo clase:   $url/clase"
    echo "  Celular, registro:     $url/registro"
    echo "  Panel (en la laptop):  https://localhost:8000/control"
else
    echo "  Enlace para registrarse:  $url/registro"
fi
echo
qrencode -t ANSIUTF8 "$url/$pagina" 2>/dev/null || true
echo "El enlace cambia cada vez que abres el túnel. Ctrl+C para cerrarlo."
wait $tunel
