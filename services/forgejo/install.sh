#!/bin/sh
set -e
# Service: Forgejo Git Server

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Forgejo Git on 127.0.0.1:3000 (Domain: https://git.$BASE_DOMAIN, SSH: 2222)..."

if ! command -v forgejo >/dev/null 2>&1; then
    apk_add forgejo forgejo-openrc
fi

addgroup forgejo media 2>/dev/null || true

mkdir -p /etc/forgejo /var/lib/forgejo/data /var/log/forgejo
chown -R forgejo:forgejo /var/log/forgejo 2>/dev/null || true

cat << EOF > /etc/forgejo/app.ini
APP_NAME = Homelab Git
RUN_USER = forgejo
RUN_MODE = prod

[log]
MODE = file
LEVEL = Info
ROOT_PATH = /var/log/forgejo

[log.file]
FILE_NAME = forgejo.log

[server]
DOMAIN = git.$BASE_DOMAIN
HTTP_ADDR = 127.0.0.1
HTTP_PORT = 3000
ROOT_URL = https://git.$BASE_DOMAIN/
DISABLE_SSH = false
SSH_PORT = 2222
SSH_LISTEN_PORT = 2222
START_SSH_SERVER = true
APP_DATA_PATH = /var/lib/forgejo/data

[database]
DB_TYPE = sqlite3
PATH = /var/lib/forgejo/data/forgejo.db

[security]
INSTALL_LOCK = true
EOF

chown -R forgejo:www-data /etc/forgejo /var/lib/forgejo
rc-update add forgejo default >/dev/null 2>&1 || true
rc-service forgejo restart >/dev/null 2>&1 || true

# Provision primary administrator account seamlessly
log_info "Ensuring initial Forgejo administrator account ($AUTH_USER)..."
for _ in 1 2 3; do
    if [ -f /var/lib/forgejo/data/forgejo.db ]; then
        su -s /bin/sh forgejo -c "forgejo admin user create --admin --username '$AUTH_USER' --password '$AUTH_PASS' --email '$AUTH_USER@$BASE_DOMAIN' --must-change-password=false" >/dev/null 2>&1 || true
        break
    fi
    sleep 1
done

deploy_caddy_snippet "forgejo"

log_success "Forgejo Git running on https://git.$BASE_DOMAIN"
