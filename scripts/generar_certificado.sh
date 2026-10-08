#!/usr/bin/env bash
# Genera un certificado autofirmado para servir la página por HTTPS.
# Vale para 10.42.0.1 (hotspot), las IPs que la laptop tenga ahora (red de casa),
# 127.0.0.1 y localhost. IPs extra:  ./scripts/generar_certificado.sh 192.168.1.50
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p certs

ips=(10.42.0.1 127.0.0.1)
ips+=($(ip -4 -o addr show scope global | awk '{split($4, a, "/"); print a[1]}'))
ips+=("$@")
san="DNS:localhost"
for ip in $(printf '%s\n' "${ips[@]}" | sort -u); do san+=",IP:$ip"; done

openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 825 \
    -keyout certs/clave.pem -out certs/certificado.pem \
    -subj "/CN=Aula que Escucha" \
    -addext "subjectAltName=$san" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null
chmod 600 certs/clave.pem
echo "Certificado creado en certs/ para: ${san//,/ }"
