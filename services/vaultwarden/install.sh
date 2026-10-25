#!/bin/sh
set -e
# Service: Vaultwarden (Bitwarden compatible password manager)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Vaultwarden on 127.0.0.1:8000 (Domain: https://vault.$BASE_DOMAIN)..."

if ! command -v vaultwarden >/dev/null 2>&1; then
    apk add --no-cache vaultwarden vaultwarden-openrc vaultwarden-web-vault
fi

cat << EOF > /etc/conf.d/vaultwarden
ROCKET_ADDRESS=127.0.0.1
ROCKET_PORT=8000
DOMAIN=https://vault.$BASE_DOMAIN
WEB_VAULT_ENABLED=true
WEB_VAULT_FOLDER=/usr/share/webapps/vaultwarden-web/
SIGNUPS_ALLOWED=true
EOF

mkdir -p /var/lib/vaultwarden
cat << EOF > /var/lib/vaultwarden/.env
ROCKET_ADDRESS=127.0.0.1
ROCKET_PORT=8000
DOMAIN=https://vault.$BASE_DOMAIN
WEB_VAULT_ENABLED=true
WEB_VAULT_FOLDER=/usr/share/webapps/vaultwarden-web/
SIGNUPS_ALLOWED=true
EOF

chown -R vaultwarden:vaultwarden /var/lib/vaultwarden
rc-update add vaultwarden default >/dev/null 2>&1 || true
rc-service vaultwarden restart >/dev/null 2>&1 || true

log_success "Vaultwarden running on https://vault.$BASE_DOMAIN"
