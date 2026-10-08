#!/bin/sh
# Instala hotspot-firewall como root y permite a este usuario ejecutarlo sin contraseña.
# Uso: sudo sh instalar-sin-clave.sh        Deshacer: sudo sh instalar-sin-clave.sh --quitar
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Ejecútalo con sudo." >&2; exit 1; }
user=${SUDO_USER:?Ejecútalo con sudo desde tu usuario, no como root directo}
dir=$(dirname "$(readlink -f "$0")")

if [ "${1:-}" = --quitar ]; then
    rm -f /etc/sudoers.d/hotspot /usr/local/bin/hotspot-firewall
    echo "Quitado: vuelve a pedir contraseña."
    exit 0
fi

# Copia propiedad de root: tu usuario no puede modificarla después.
install -o root -g root -m 755 "$dir/hotspot-firewall" /usr/local/bin/hotspot-firewall

tmp=$(mktemp)
printf '%s ALL=(root) NOPASSWD: /usr/local/bin/hotspot-firewall\n' "$user" >"$tmp"
visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; echo "Regla de sudoers inválida; no se instaló." >&2; exit 1; }
install -o root -g root -m 440 "$tmp" /etc/sudoers.d/hotspot
rm -f "$tmp"
echo "Listo: /usr/local/bin/hotspot-firewall y /etc/sudoers.d/hotspot instalados para '$user'."
