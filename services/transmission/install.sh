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
    apk_add transmission-daemon transmission-remote transmission-daemon-openrc
fi

addgroup transmission media 2>/dev/null || true

mkdir -p /var/lib/transmission/config "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete" /var/log/transmission /etc/transmission /etc/periodic/weekly
chown -R transmission:media /var/log/transmission /var/lib/transmission /etc/transmission 2>/dev/null || true
chown -R "${SAFE_USER:-$AUTH_USER}:media" "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete" 2>/dev/null || true
chmod 2775 "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete" 2>/dev/null || true

cat << 'EOF' > /etc/conf.d/transmission-daemon
logfile="/var/log/transmission/transmission.log"
TRANSMISSION_OPTIONS="--log-info"
EOF

# Stop daemon before updating settings.json (Transmission overwrites settings on exit)
rc-service transmission-daemon stop >/dev/null 2>&1 || true

cat << EOF > /var/lib/transmission/config/settings.json
{
    "alt-speed-down": 5000,
    "alt-speed-enabled": false,
    "alt-speed-up": 1000,
    "bind-address-ipv4": "0.0.0.0",
    "blocklist-enabled": true,
    "blocklist-url": "https://raw.githubusercontent.com/Naunter/BT_BlockLists/master/bt_blocklists.gz",
    "cache-size-mb": 16,
    "dht-enabled": true,
    "download-dir": "$STORAGE_BASE/downloads",
    "download-queue-enabled": true,
    "download-queue-size": 5,
    "encryption": 1,
    "idle-seeding-limit": 30,
    "idle-seeding-limit-enabled": false,
    "incomplete-dir": "$STORAGE_BASE/incomplete",
    "incomplete-dir-enabled": true,
    "lpd-enabled": false,
    "message-level": 1,
    "peer-congestion-algorithm": "bbr",
    "peer-limit-global": 200,
    "peer-limit-per-torrent": 50,
    "peer-port": 51413,
    "peer-port-random-on-start": false,
    "peer-socket-tos": "lowcost",
    "pex-enabled": true,
    "port-forwarding-enabled": false,
    "preallocation": 1,
    "prefetch-enabled": true,
    "queue-stalled-enabled": true,
    "queue-stalled-minutes": 30,
    "ratio-limit": 2.0,
    "ratio-limit-enabled": true,
    "rename-partial-files": true,
    "rpc-authentication-required": true,
    "rpc-bind-address": "127.0.0.1",
    "rpc-enabled": true,
    "rpc-host-whitelist-enabled": false,
    "rpc-password": "$AUTH_PASS",
    "rpc-port": 9091,
    "rpc-url": "/transmission/",
    "rpc-username": "$AUTH_USER",
    "rpc-whitelist": "127.0.0.1,::1",
    "rpc-whitelist-enabled": true,
    "scrape-paused-torrents-enabled": false,
    "script-torrent-done-enabled": true,
    "script-torrent-done-filename": "/etc/transmission/torrent-done.sh",
    "seed-queue-enabled": true,
    "seed-queue-size": 10,
    "start-added-torrents": true,
    "trash-original-torrent-files": true,
    "umask": 2,
    "upload-slots-per-torrent": 14,
    "utp-enabled": false
}
EOF

# Deploy Transmission completion hook: auto-hardlinks music and triggers Navidrome startScan API
cat << EOF > /etc/transmission/torrent-done.sh
#!/bin/sh
# Hook: Transmission finished download -> Auto-detect audio -> Instant Navidrome Subsonic scan
set -eu

ND_URL="http://127.0.0.1:4533"
ND_USER="$AUTH_USER"
ND_PASS="$AUTH_PASS"
MUSIC_DIR="$STORAGE_BASE/music"

TARGET_PATH="\${TR_TORRENT_DIR:-$STORAGE_BASE/downloads}/\${TR_TORRENT_NAME:-}"
[ ! -e "\$TARGET_PATH" ] && exit 0

HAS_AUDIO=0
if [ -f "\$TARGET_PATH" ]; then
    case "\$TARGET_PATH" in
        *.flac|*.FLAC|*.mp3|*.MP3|*.opus|*.OPUS|*.m4a|*.M4A|*.ogg|*.OGG|*.wav|*.WAV|*.alac) HAS_AUDIO=1 ;;
    esac
elif [ -d "\$TARGET_PATH" ]; then
    if find "\$TARGET_PATH" -type f \\( -iname "*.flac" -o -iname "*.mp3" -o -iname "*.opus" -o -iname "*.m4a" -o -iname "*.ogg" -o -iname "*.wav" \\) -print -quit 2>/dev/null | grep -q .; then
        HAS_AUDIO=1
    fi
fi

[ "\$HAS_AUDIO" -eq 0 ] && exit 0

case "\${TR_TORRENT_DIR:-}" in
    "\$MUSIC_DIR"*) ;;
    *)
        if [ -d "\$MUSIC_DIR" ]; then
            cp -al "\$TARGET_PATH" "\$MUSIC_DIR/" 2>/dev/null || cp -r "\$TARGET_PATH" "\$MUSIC_DIR/" 2>/dev/null || true
            chown -R $AUTH_USER:media "\$MUSIC_DIR" 2>/dev/null || true
        fi
        ;;
esac

if [ -n "\$ND_PASS" ]; then
    SALT=\$(head -c 8 /dev/urandom 2>/dev/null | od -An -tx1 | tr -d ' \\n' || echo "homelabsalt")
    TOKEN=\$(printf '%s%s' "\$ND_PASS" "\$SALT" | md5sum | awk '{print \$1}')
    curl -sS --max-time 5 \\
        "\${ND_URL}/rest/startScan.view?u=\${ND_USER}&t=\${TOKEN}&s=\${SALT}&v=1.16.1&c=transmission-hook&f=json" \\
        >/dev/null 2>&1 || true
fi
EOF
chmod 755 /etc/transmission/torrent-done.sh
chown transmission:media /etc/transmission/torrent-done.sh

# Deploy weekly blocklist update script
cat << EOF > /etc/periodic/weekly/transmission-blocklist
#!/bin/sh
transmission-remote 127.0.0.1:9091 -n "$AUTH_USER:$AUTH_PASS" --blocklist-update >/dev/null 2>&1 || true
EOF
chmod 755 /etc/periodic/weekly/transmission-blocklist

chown -R transmission:media /var/lib/transmission
rc-update add transmission-daemon default >/dev/null 2>&1 || true
rc-service transmission-daemon restart >/dev/null 2>&1 || true

deploy_caddy_snippet "transmission"

log_success "Transmission running on https://torrent.$BASE_DOMAIN"
