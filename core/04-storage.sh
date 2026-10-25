#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Initializing storage layout at $STORAGE_BASE..."

mkdir -p "$STORAGE_BASE/music" "$STORAGE_BASE/downloads" "$STORAGE_BASE/incomplete"
addgroup -g 1000 media 2>/dev/null || true
adduser -D -u 1000 -G media "$AUTH_USER" 2>/dev/null || true
echo "$AUTH_USER:$AUTH_PASS" | chpasswd

# Add service daemons to media group
for u in caddy transmission navidrome forgejo; do
    addgroup "$u" media 2>/dev/null || true
done

chown -R "$AUTH_USER:media" "$STORAGE_BASE"
chmod -R 2775 "$STORAGE_BASE"

log_success "Storage layout created with media group permissions."
