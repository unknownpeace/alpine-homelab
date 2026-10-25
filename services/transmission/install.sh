#!/bin/sh
set -e
# Service: Transmission BitTorrent Client

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Transmission on 127.0.0.1:9091 (Downloads: $STORAGE_BASE/downloads)..."

if ! command -v transmission-daemon >/dev/null 2>&1; then
    apk_add transmission-daemon transmission-daemon-openrc
fi

mkdir -p /var/lib/transmission/config "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete"
cat << EOF > /var/lib/transmission/config/settings.json
{
    "bind-address-ipv4": "0.0.0.0",
    "dht-enabled": true,
    "download-dir": "$STORAGE_BASE/downloads",
    "incomplete-dir": "$STORAGE_BASE/incomplete",
    "incomplete-dir-enabled": true,
    "peer-port": 51413,
    "pex-enabled": true,
    "rpc-authentication-required": true,
    "rpc-bind-address": "127.0.0.1",
    "rpc-enabled": true,
    "rpc-host-whitelist-enabled": false,
    "rpc-password": "$AUTH_PASS",
    "rpc-port": 9091,
    "rpc-url": "/transmission/",
    "rpc-username": "$AUTH_USER",
    "rpc-whitelist-enabled": false,
    "umask": 2,
    "port-forwarding-enabled": false,
    "utp-enabled": true
}
EOF

chown -R transmission:media /var/lib/transmission
rc-update add transmission-daemon default >/dev/null 2>&1 || true
rc-service transmission-daemon restart >/dev/null 2>&1 || true

deploy_caddy_snippet "transmission"

log_success "Transmission running on https://torrent.$BASE_DOMAIN"
