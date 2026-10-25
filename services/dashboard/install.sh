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

# Expose Caddy root certificate for download via web UI
if [ -f /root/caddy-homelab-root.crt ]; then
    cp -f /root/caddy-homelab-root.crt /var/www/dashboard/caddy-homelab-root.crt
fi

chown -R caddy:caddy /var/www/dashboard 2>/dev/null || true

log_success "Homelab Dashboard deployed at https://homelab.$BASE_DOMAIN"
