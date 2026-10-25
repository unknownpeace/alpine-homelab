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
    python3 py3-requests py3-bottle py3-waitress \
    bridge sqlite git openssl restic

# Apply system timezone if configured
if [ -n "$TIMEZONE" ]; then
    log_info "Setting system timezone to $TIMEZONE..."
    setup-timezone -z "$TIMEZONE" 2>/dev/null || true
fi

# Enable and start NTP synchronization daemon (prevent clock drift & SSL/GPG verification errors)
rc-update add ntpd default >/dev/null 2>&1 || true
rc-service ntpd restart >/dev/null 2>&1 || true

log_success "System packages installed, timezone configured and NTP synchronized."
