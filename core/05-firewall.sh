#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring hardened Zero-Trust nftables firewall, dynamic brute-force sets & NAT rules..."

extra_masq=""
if [ "${GATEWAY_MODE:-yes}" = "yes" ]; then
    extra_masq="        oifname \"br0\" masquerade"
    if [ -n "$PHYSICAL_IF" ] && [ "$PHYSICAL_IF" != "br0" ]; then
        extra_masq="$extra_masq
        oifname \"$PHYSICAL_IF\" masquerade"
    fi
fi

forward_mss=""
if [ "${GATEWAY_MODE:-yes}" = "yes" ]; then
    forward_mss="        tcp flags syn tcp option maxseg size set rt mtu"
fi

cat << EOF > /etc/nftables.nft
#!/usr/sbin/nft -f
flush ruleset

table inet homelab {
    # --------------------------------------------------------------------------
    # 1. Dynamic Stateful Sets for Brute-Force & Flood Protection (Zero-Daemon)
    # --------------------------------------------------------------------------
    set ssh_banlist {
        type ipv4_addr
        size 65535
        flags dynamic, timeout
        timeout 15m
    }

    set ssh_meter {
        type ipv4_addr
        size 65535
        flags dynamic, timeout
        timeout 1m
    }

    set web_banlist {
        type ipv4_addr
        size 65535
        flags dynamic, timeout
        timeout 10m
    }

    set web_meter {
        type ipv4_addr
        size 65535
        flags dynamic, timeout
        timeout 1m
    }

    set portscan_meter {
        type ipv4_addr
        size 65535
        flags dynamic, timeout
        timeout 5m
    }

    # --------------------------------------------------------------------------
    # 2. Input Filter Chain (Host Protection)
    # --------------------------------------------------------------------------
    chain input {
        type filter hook input priority filter; policy accept;

        # Always allow loopback and drop invalid conntrack states first
        iifname "lo" accept
        ct state invalid drop

        # Drop banned IPs BEFORE established/related so active HTTP/2 or SSH sessions are severed immediately
        ip saddr @ssh_banlist counter drop
        ip saddr @web_banlist counter drop

        # Drop bogus TCP flags (NULL, XMAS, SYN+FIN, SYN+RST scans)
        tcp flags & (fin|syn|rst|psh|ack|urg) == 0x0 counter drop
        tcp flags & (fin|psh|urg) == (fin|psh|urg) counter drop
        tcp flags & (syn|fin) == (syn|fin) counter drop
        tcp flags & (syn|rst) == (syn|rst) counter drop

        # Fast-path for established and related traffic
        ct state { established, related } accept

        # ICMP rate limiting (prevent ping flood while preserving Path MTU Discovery)
        ip protocol icmp icmp type { echo-request } limit rate 10/second burst 20 packets accept
        ip protocol icmp icmp type { echo-request } drop

        # Trusted Management Networks (Localhost, RFC1918 Private LAN) - bypass brute-force limits
        ip saddr { 127.0.0.1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } tcp dport { 22, 2222 } accept

        # Untrusted / Public SSH & Forgejo Git SSH (ports 22, 2222) Brute-Force Rate Limiting:
        # Rate limit over 15 new connections per minute (burst 30) per source IP; violators banned for 15m
        tcp dport { 22, 2222 } ct state new update @ssh_meter { ip saddr limit rate over 15/minute burst 30 packets } \
            add @ssh_banlist { ip saddr timeout 15m } log prefix "[NFT-SSH-BAN] " counter drop

        # Caddy HTTP/HTTPS (ports 80, 443) Connection & Credential-Stuffing Protection:
        # 1) Cap concurrent TCP connections per IP to 80
        tcp dport { 80, 443 } ct count over 80 counter reject with tcp reset
        # 2) Ban IPs opening > 45 new connections/sec (burst 90) for 10m
        tcp dport { 80, 443 } ct state new update @web_meter { ip saddr limit rate over 45/second burst 90 packets } \
            add @web_banlist { ip saddr timeout 10m } log prefix "[NFT-WEB-BAN] " counter drop
        udp dport 443 ct state new update @web_meter { ip saddr limit rate over 60/second burst 120 packets } \
            add @web_banlist { ip saddr timeout 10m } counter drop
    }

    # --------------------------------------------------------------------------
    # 3. NAT Prerouting & Postrouting Chains (LXC & Gateway)
    # --------------------------------------------------------------------------
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
    }

    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname != "lxcbr0" ip saddr 10.0.3.0/24 masquerade
$extra_masq
    }

    # --------------------------------------------------------------------------
    # 4. Forward Filter Chain
    # --------------------------------------------------------------------------
    chain forward {
        type filter hook forward priority filter; policy accept;
        ct state invalid drop
$forward_mss
    }
}
EOF

chmod 600 /etc/nftables.nft
rc-update add nftables default >/dev/null 2>&1 || true
rc-service nftables restart >/dev/null 2>&1 || nft -f /etc/nftables.nft

# Re-synchronize LXC DNAT port-forwards if LXC Manager is installed
if [ -f /opt/lxc-manager/app.py ]; then
    python3 -c "import sys; sys.path.insert(0, '/opt/lxc-manager'); import app; app.sync_all_port_forwards()" 2>/dev/null || true
fi

log_success "Zero-Trust nftables rules and dynamic brute-force sets (@ssh_banlist, @web_banlist) active."
