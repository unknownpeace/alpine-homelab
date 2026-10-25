#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Applying high-performance kernel parameters (BBR / FQ / ip_forward)..."

mkdir -p /etc/sysctl.d
cat << 'EOF' > /etc/sysctl.d/99-homelab.conf
net.ipv4.ip_forward = 1
net.ipv4.ip_unprivileged_port_start = 53
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
EOF
sysctl -p /etc/sysctl.d/99-homelab.conf >/dev/null 2>&1 || true

log_success "Kernel parameters applied."
