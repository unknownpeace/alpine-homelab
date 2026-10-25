#!/bin/sh
set -e
# Service: Conduit Matrix Server

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Conduit Matrix on 127.0.0.1:6167 (Server: matrix.$BASE_DOMAIN)..."

mkdir -p /etc/conduit /var/lib/conduit
cat << EOF > /etc/conduit/conduit.toml
[global]
server_name = "matrix.$BASE_DOMAIN"
database_backend = "rocksdb"
database_path = "/var/lib/conduit"
address = "127.0.0.1"
port = 6167
allow_registration = true
allow_federation = false
trusted_servers = []
max_request_size = 20_000_000
EOF

chown -R conduit:conduit /var/lib/conduit /etc/conduit
rc-update add conduit default >/dev/null 2>&1 || true
rc-service conduit restart >/dev/null 2>&1 || true

log_success "Conduit Matrix running on https://matrix.$BASE_DOMAIN"
