#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring nftables firewall and NAT rules..."

if [ "${GATEWAY_MODE:-yes}" = "yes" ]; then
    extra_masq=""
    if [ -n "$PHYSICAL_IF" ] && [ "$PHYSICAL_IF" != "br0" ]; then
        extra_masq="        oifname \"$PHYSICAL_IF\" masquerade"
    fi
cat << EOF > /etc/nftables.nft
table inet homelab {
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
        tcp flags syn tcp option maxseg size set rt mtu
    }
}
EOF
else
cat << EOF > /etc/nftables.nft
table inet homelab {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname != "lxcbr0" ip saddr 10.0.3.0/24 masquerade
    }
    chain forward {
        type filter hook forward priority filter; policy accept;
    }
}
EOF
fi

rc-update add nftables default >/dev/null 2>&1 || true
rc-service nftables restart >/dev/null 2>&1 || true

log_success "nftables rules active (Gateway mode: ${GATEWAY_MODE:-yes})."
