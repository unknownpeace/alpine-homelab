#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Applying Principal-grade kernel parameters (TCP BBR, QUIC buffers, Conntrack, VM & TUN routing)..."

# Load nf_conntrack and tcp_bbr modules early so sysctl keys exist
modprobe nf_conntrack 2>/dev/null || true
modprobe tcp_bbr 2>/dev/null || true
for mod in nf_conntrack tcp_bbr; do
    if ! grep -q "^${mod}$" /etc/modules 2>/dev/null; then
        echo "$mod" >> /etc/modules 2>/dev/null || true
    fi
done

mkdir -p /etc/sysctl.d
cat << 'EOF' > /etc/sysctl.d/99-homelab.conf
# ==============================================================================
# Alpine Homelab 3.24 - High-Performance Gateway & Low-Memory Appliance Tuning
# ==============================================================================

# 1. IP Forwarding & Unprivileged DNS Port (sing-box / AdGuard Home)
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.ip_unprivileged_port_start = 53

# 2. Loose Reverse Path Filtering (Required for sing-box TUN & br0 policy routing)
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2

# 3. Google BBR Congestion Control & Fair Queueing
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# 4. Socket Buffers for HTTP/3 QUIC (Caddy 2), Hysteria2 (sing-box), SMB3 & BitTorrent
net.core.rmem_default = 262144
net.core.wmem_default = 262144
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.optmem_max = 65536
net.ipv4.tcp_rmem = 4096 131072 16777216
net.ipv4.tcp_wmem = 4096 65536 16777216
net.ipv4.udp_rmem_min = 16384
net.ipv4.udp_wmem_min = 16384

# 5. Network Interface Backlog & Listen Queues
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192
net.ipv4.tcp_max_syn_backlog = 8192

# 6. Low-Latency TCP & Proxy Multiplexing Optimizations
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_notsent_lowat = 16384
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_max_tw_buckets = 65536
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_intvl = 30
net.ipv4.tcp_keepalive_probes = 5
net.ipv4.ip_local_port_range = 10240 65535

# 7. Network Stack Security Hardening (Anti-Spoofing, SYN Cookies, ICMP Redirects)
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_rfc1337 = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# 8. Netfilter Conntrack Tuning (NAT Gateway + LXC + BitTorrent DHT)
net.netfilter.nf_conntrack_max = 131072
net.netfilter.nf_conntrack_tcp_timeout_established = 7200
net.netfilter.nf_conntrack_tcp_timeout_time_wait = 30
net.netfilter.nf_conntrack_tcp_timeout_close_wait = 15
net.netfilter.nf_conntrack_udp_timeout = 30
net.netfilter.nf_conntrack_udp_timeout_stream = 120

# 9. Virtual Memory, VFS Cache & File Descriptors (< 450 MB RAM Target)
vm.swappiness = 10
vm.vfs_cache_pressure = 50
vm.dirty_background_ratio = 5
vm.dirty_ratio = 15
vm.overcommit_memory = 1
fs.file-max = 2097152
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 1024
kernel.panic = 10
EOF

sysctl -e -p /etc/sysctl.d/99-homelab.conf >/dev/null 2>&1 || true

log_success "Kernel parameters applied (TCP BBR, 16MB QUIC buffers, Conntrack 128k, VM tuned)."
