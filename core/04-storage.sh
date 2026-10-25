#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Initializing storage layout at $STORAGE_BASE and isolated backup vault at ${BACKUP_DIR:-/var/backups/homelab}..."

mkdir -p "$STORAGE_BASE/music" "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete"
mkdir -p "${BACKUP_DIR:-/var/backups/homelab}" /etc/homelab
chmod 700 "${BACKUP_DIR:-/var/backups/homelab}" /etc/homelab

addgroup -g 1000 media 2>/dev/null || addgroup media 2>/dev/null || true

# Sanitize username to valid POSIX account (lowercase alphanumeric)
SAFE_USER=$(echo "$AUTH_USER" | tr -cd 'a-zA-Z0-9_-' | tr '[:upper:]' '[:lower:]')
[ -z "$SAFE_USER" ] && SAFE_USER="admin"

if ! id -u "$SAFE_USER" >/dev/null 2>&1; then
    adduser -D -u 1000 -G media "$SAFE_USER" 2>/dev/null || adduser -D -G media "$SAFE_USER" 2>/dev/null || true
fi
if id -u "$SAFE_USER" >/dev/null 2>&1; then
    echo "$SAFE_USER:$AUTH_PASS" | chpasswd
    addgroup "$SAFE_USER" media 2>/dev/null || true
fi

# Add existing service daemons to media group
for u in caddy transmission navidrome forgejo ytdlp; do
    if id -u "$u" >/dev/null 2>&1; then
        addgroup "$u" media 2>/dev/null || true
    fi
done

chown -R "$SAFE_USER:media" "$STORAGE_BASE/music" "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete"
chmod -R 2775 "$STORAGE_BASE/music" "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete"

log_success "Storage layout created with media group permissions and isolated 0700 backup vault."
