#!/bin/sh
set -e
# Service: Tailscale Zero-Trust Mesh VPN

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Installing Tailscale Mesh VPN packages..."

apk add --no-cache tailscale tailscale-openrc

# Ensure TUN kernel module is active
modprobe tun 2>/dev/null || true

rc-update add tailscale default >/dev/null 2>&1 || true
rc-service tailscale restart >/dev/null 2>&1 || true

log_success "Tailscale service running. Use 'homelab tailscale up' to connect."
