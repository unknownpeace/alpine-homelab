#!/bin/sh
set -e
# Service: Navidrome Music Server

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Navidrome Music Server on 127.0.0.1:4533 (Music: $STORAGE_BASE/music)..."

if ! command -v navidrome >/dev/null 2>&1; then
    apk add --no-cache navidrome navidrome-openrc ffmpeg
fi

mkdir -p /etc/navidrome /var/lib/navidrome "$STORAGE_BASE/music"
cat << EOF > /etc/navidrome/navidrome.toml
MusicFolder = "$STORAGE_BASE/music"
DataFolder = "/var/lib/navidrome"
Address = "127.0.0.1"
Port = 4533
ScanSchedule = "@every 1m"
TranscodingCacheSize = "100MB"
SessionTimeout = "48h"
EnableInsightsCollector = false
EOF

chown -R navidrome:media /var/lib/navidrome /etc/navidrome
rc-update add navidrome default >/dev/null 2>&1 || true
rc-service navidrome restart >/dev/null 2>&1 || true

log_success "Navidrome running on https://music.$BASE_DOMAIN"
