#!/bin/sh
set -e
# Service: Navidrome Music Server (SQLite WAL & Subsonic Tuned)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Navidrome Music Server on 127.0.0.1:4533 (Music: $STORAGE_BASE/music)..."

if ! command -v navidrome >/dev/null 2>&1; then
    apk_add navidrome navidrome-openrc ffmpeg sqlite
fi

addgroup navidrome media 2>/dev/null || true

mkdir -p /etc/navidrome /var/lib/navidrome "$STORAGE_BASE/music" /etc/conf.d
touch /var/log/navidrome.log
chown navidrome:media /var/log/navidrome.log 2>/dev/null || true
chown -R "${SAFE_USER:-admin}:media" "$STORAGE_BASE/music" 2>/dev/null || true
chmod 2775 "$STORAGE_BASE/music" 2>/dev/null || true

cat << 'EOF' > /etc/conf.d/navidrome
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
navidrome_configfile=/etc/navidrome/navidrome.toml
EOF

cat << EOF > /etc/navidrome/navidrome.toml
MusicFolder = "$STORAGE_BASE/music"
DataFolder = "/var/lib/navidrome"
DbPath = "/var/lib/navidrome/navidrome.db?_journal_mode=WAL&_synchronous=NORMAL&_busy_timeout=15000&_cache_size=-16384&_temp_store=MEMORY&_mmap_size=67108864&cache=shared"
Address = "127.0.0.1"
Port = 4533
ScanSchedule = "@every 5m"
TranscodingCacheSize = "100MB"
ImageCacheSize = "50MB"
SessionTimeout = "48h"
EnableInsightsCollector = false
LogLevel = "info"
LogFile = "/var/log/navidrome.log"
EOF

chown -R navidrome:media /var/lib/navidrome /etc/navidrome
rc-update add navidrome default >/dev/null 2>&1 || true
rc-service navidrome restart >/dev/null 2>&1 || true

for _ in 1 2 3; do
    if [ -f /var/lib/navidrome/navidrome.db ]; then
        sqlite3 /var/lib/navidrome/navidrome.db \
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-16384; PRAGMA temp_store=MEMORY; PRAGMA mmap_size=67108864; PRAGMA busy_timeout=15000; PRAGMA wal_autocheckpoint=1000; PRAGMA optimize;" >/dev/null 2>&1 || true
        chown -R navidrome:media /var/lib/navidrome
        break
    fi
    sleep 1
done

deploy_caddy_snippet "navidrome"

log_success "Navidrome running on https://music.$BASE_DOMAIN (SQLite WAL + synchronous=NORMAL active)"
