#!/bin/sh
set -e
# Service: Matrix Homeserver (Tuwunel 2026 / Conduit with Registration Token Protection)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Matrix Homeserver on 127.0.0.1:6167 (Server: matrix.$BASE_DOMAIN)..."

# Prefer Tuwunel if available in repository, fallback to Conduit
if apk info -e tuwunel >/dev/null 2>&1 || apk search -q tuwunel 2>/dev/null | grep -q "^tuwunel$"; then
    apk_add tuwunel tuwunel-openrc 2>/dev/null || apk_add conduit conduit-openrc
elif ! command -v conduit >/dev/null 2>&1; then
    apk_add conduit conduit-openrc
fi

mkdir -p /etc/conduit /var/lib/conduit /etc/homelab /etc/conf.d
chmod 700 /etc/homelab

cat << 'EOF' > /etc/conf.d/conduit
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
export CONDUIT_CONFIG=/etc/conduit/conduit.toml
EOF

REG_TOKEN_FILE="/etc/homelab/matrix_registration_token.txt"
if [ ! -f "$REG_TOKEN_FILE" ]; then
    tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 24 > "$REG_TOKEN_FILE" || echo "HomelabMatrix2026" > "$REG_TOKEN_FILE"
    chmod 600 "$REG_TOKEN_FILE"
fi
REG_TOKEN=$(cat "$REG_TOKEN_FILE")

cat << EOF > /etc/conduit/conduit.toml
[global]
server_name = "matrix.$BASE_DOMAIN"
database_backend = "rocksdb"
database_path = "/var/lib/conduit"
address = "127.0.0.1"
port = 6167
db_cache_capacity_mb = 24.0
pdu_cache_capacity = 50000
cleanup_second_interval = 3600
rocksdb_compression_algo = "zstd"
max_request_size = 52_428_800
allow_registration = true
registration_token = "$REG_TOKEN"
allow_federation = false
trusted_servers = []

[global.well_known]
client = "https://matrix.$BASE_DOMAIN"
server = "matrix.$BASE_DOMAIN:443"
EOF

chown -R conduit:conduit /var/lib/conduit /etc/conduit
chmod 640 /etc/conduit/conduit.toml
rc-update add conduit default >/dev/null 2>&1 || true
rc-service conduit restart >/dev/null 2>&1 || true

deploy_caddy_snippet "conduit"

log_success "Matrix Homeserver running on https://matrix.$BASE_DOMAIN (Registration Token: $REG_TOKEN)"
