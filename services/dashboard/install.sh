#!/bin/sh
set -e
# Service: Homelab Static Dashboard (https://homelab.lan & https://IP_HOST)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying Homelab Portal Dashboard to /var/www/dashboard..."

mkdir -p /var/www/dashboard
cp -f "$SERVICE_DIR/index.html" /var/www/dashboard/index.html

# Generate dynamic environment configuration for frontend cards
if [ "${ENABLE_FORGEJO:-yes}" = "yes" ]; then
    RAM_EST="~450 MB (Всего)"
else
    RAM_EST="~180 MB (Минимум)"
fi

cat << EOF > /var/www/dashboard/env-config.js
window.__HOMELAB_CONFIG__ = {
  vaultwarden: "${ENABLE_VAULTWARDEN:-yes}",
  forgejo: "${ENABLE_FORGEJO:-yes}",
  lxc: "${ENABLE_LXC_MANAGER:-yes}",
  singbox: "${ENABLE_SINGBOX:-yes}",
  navidrome: "${ENABLE_NAVIDROME:-yes}",
  transmission: "${ENABLE_TRANSMISSION:-yes}",
  ytdlp: "${ENABLE_YTDLP:-yes}",
  conduit: "${ENABLE_CONDUIT:-yes}",
  samba: "${ENABLE_SAMBA:-yes}",
  beszel: "${ENABLE_BESZEL:-yes}",
  pocketid: "${ENABLE_POCKET_ID:-no}",
  crowdsec: "${ENABLE_CROWDSEC:-no}",
  adguardhome: "${ENABLE_ADGUARD_HOME:-no}",
  tailscale: "${ENABLE_TAILSCALE:-no}",
  ram_estimate: "$RAM_EST"
};
EOF

# Expose Caddy root certificate for download via web UI
if [ -f /root/caddy-homelab-root.crt ]; then
    cp -f /root/caddy-homelab-root.crt /var/www/dashboard/caddy-homelab-root.crt
fi

chown -R caddy:caddy /var/www/dashboard 2>/dev/null || true

log_success "Homelab Dashboard deployed at https://homelab.$BASE_DOMAIN"
