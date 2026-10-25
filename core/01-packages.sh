#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Alpine repositories and installing system packages..."

if ! grep -q "^http.*/community" /etc/apk/repositories; then
    sed -i '/^#.*\/community/s/^#//' /etc/apk/repositories || true
fi
apk update

apk_add \
    ca-certificates curl tar gzip xz \
    libcap libcap-utils \
    tzdata alpine-conf \
    nftables nftables-openrc \
    python3 py3-requests py3-bottle \
    bridge sqlite git

# Apply system timezone if configured
if [ -n "$TIMEZONE" ]; then
    log_info "Setting system timezone to $TIMEZONE..."
    setup-timezone -z "$TIMEZONE" 2>/dev/null || true
fi

log_success "System packages installed and timezone configured."
