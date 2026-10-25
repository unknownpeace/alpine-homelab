#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring network bridges..."

# 1. Setup Home LAN bridge (br0) enslaved to physical interface for LXC router-assigned DHCP
if [ "${BRIDGE_ENABLE:-yes}" = "yes" ] && [ "$PHYSICAL_IF" != "br0" ] && ! grep -q 'iface br0' /etc/network/interfaces; then
    log_info "Transitioning network to LAN bridge (br0) enslaved to $PHYSICAL_IF..."
    
    # Update /etc/network/interfaces for persistence across reboots
    sed -i -E "s/^[[:space:]]*iface[[:space:]]+$PHYSICAL_IF[[:space:]]+inet[[:space:]]+(dhcp|static).*/iface $PHYSICAL_IF inet manual/" /etc/network/interfaces 2>/dev/null || true
cat << EOF >> /etc/network/interfaces

auto br0
iface br0 inet dhcp
    bridge-ports $PHYSICAL_IF
    bridge-stp 0
EOF

    # Atomic live transition without losing SSH connection or IP
    ip link add name br0 type bridge 2>/dev/null || true
    ip link set dev br0 up 2>/dev/null || true
    ip addr flush dev "$PHYSICAL_IF" 2>/dev/null || true
    ip addr add "${IP_HOST}/${CIDR_PREFIX}" dev br0 2>/dev/null || true
    if [ -n "$DEFAULT_GW" ]; then
        ip route add default via "$DEFAULT_GW" dev br0 2>/dev/null || true
    fi
    ip link set dev "$PHYSICAL_IF" master br0 2>/dev/null || true
    ip link set dev "$PHYSICAL_IF" up 2>/dev/null || true
fi

# 2. Enable TUN kernel module (for sing-box transparent proxy)
modprobe tun 2>/dev/null || true
if ! grep -q "^tun$" /etc/modules 2>/dev/null; then
    echo "tun" >> /etc/modules || true
fi

log_success "Network bridges initialized (br0: Home LAN)."
