#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring hardened nftables firewall and NAT rules..."

if [ "${GATEWAY_MODE:-yes}" = "yes" ]; then
    extra_masq=""
    if [ -n "$PHYSICAL_IF" ] && [ "$PHYSICAL_IF" != "br0" ]; then
        extra_masq="        oifname \"$PHYSICAL_IF\" masquerade"
    fi
cat << EOF > /etc/nftables.nft
table inet homelab {
    chain input {
        type filter hook input priority filter; policy accept;
        ct state invalid drop
        iifname "lo" accept
        ct state { established, related } accept
    }
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname != "lxcbr0" ip saddr 10.0.3.0/24 masquerade
        oifname "br0" masquerade
$extra_masq
    }
    chain forward {
        type filter hook forward priority filter; policy accept;
        ct state invalid drop
        tcp flags syn tcp option maxseg size set rt mtu
    }
}
EOF
else
cat << EOF > /etc/nftables.nft
table inet homelab {
    chain input {
        type filter hook input priority filter; policy accept;
        ct state invalid drop
        iifname "lo" accept
        ct state { established, related } accept
    }
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname != "lxcbr0" ip saddr 10.0.3.0/24 masquerade
    }
    chain forward {
        type filter hook forward priority filter; policy accept;
        ct state invalid drop
    }
}
EOF
fi

rc-update add nftables default >/dev/null 2>&1 || true
rc-service nftables restart >/dev/null 2>&1 || true

# Re-synchronize LXC DNAT port-forwards if LXC Manager is installed
if [ -f /opt/lxc-manager/app.py ]; then
    python3 -c "import sys; sys.path.insert(0, '/opt/lxc-manager'); import app; app.sync_all_port_forwards()" 2>/dev/null || true
fi

log_success "nftables rules active and LXC DNAT rules synchronized (Gateway mode: ${GATEWAY_MODE:-yes})."
