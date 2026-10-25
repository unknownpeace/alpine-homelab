#!/bin/sh
set -e
# Service: AdGuard Home DNS & Web Dashboard (Port 3001 & Port 53)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying AdGuard Home DNS Engine and Statistics Dashboard..."

if ! command -v /usr/bin/adguardhome >/dev/null 2>&1 && [ ! -f /usr/bin/adguardhome ]; then
    apk_add adguardhome adguardhome-openrc
fi

mkdir -p /etc/adguardhome /var/lib/adguardhome

# Generate AdGuardHome.yaml if not present
if [ ! -f /etc/adguardhome/AdGuardHome.yaml ]; then
    log_info "Generating AdGuard Home configuration (UI port 3001, upstream 127.0.0.1:5353)..."
    cat << EOF > /etc/adguardhome/AdGuardHome.yaml
bind_host: 127.0.0.1
bind_port: 3001
users: []
auth_attempts: 5
block_auth_min: 15
http_proxy: ""
language: ru
theme: auto
debug_pprof: false
web_session_ttl_hours: 720
dns:
  bind_hosts:
    - 0.0.0.0
  port: 53
  statistics_interval: 90
  querylog_enabled: true
  querylog_file_enabled: true
  querylog_interval: 2160h
  querylog_size_memory: 1000
  anonymize_client_ip: false
  protection_enabled: true
  blocking_mode: default
  blocking_ipv4: ""
  blocking_ipv6: ""
  blocked_response_ttl: 10
  parental_block_host: parental-block.adguard.org
  safebrowsing_block_host: standard-block.adguard.org
  ratelimit: 0
  ratelimit_whitelist: []
  refuse_any: true
  upstream_dns:
    - 127.0.0.1:5353
  upstream_dns_file: ""
  bootstrap_dns:
    - 1.1.1.1
    - 77.88.8.8
  all_servers: false
  fastest_addr: false
  fastest_timeout: 0s
  allowed_clients: []
  disallowed_clients: []
  blocked_hosts:
    - version.bind
    - id.server
    - hostname.bind
  trusted_proxies:
    - 127.0.0.0/8
    - ::1/128
  cache_size: 4194304
  cache_ttl_min: 0
  cache_ttl_max: 0
  cache_optimistic: false
  bogus_nxdomain: []
  aaaa_disabled: false
  enable_dnssec: false
  edns_client_subnet:
    custom_ip: ""
    enabled: false
    use_custom: false
  max_goroutines: 300
  handle_ddr: true
  ipset: []
  ipset_file: ""
  bootstrap_prefer_ipv6: false
filters:
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt
    name: AdGuard DNS filter
    id: 1
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_7.txt
    name: AdGuard Russian filter
    id: 2
user_rules: []
dhcp:
  enabled: false
clients:
  runtime_sources:
    whois: true
    arp: true
    rdns: true
    dhcp: true
    hosts: true
  persistent: []
log_file: /var/log/adguardhome.log
log_max_backups: 0
log_max_size: 100
log_max_age: 3
log_compress: false
log_localtime: false
verbose: false
os:
  group: ""
  user: ""
  rlimit_nofile: 0
schema_version: 29
EOF
fi

rc-update add adguardhome default >/dev/null 2>&1 || true
rc-service adguardhome restart >/dev/null 2>&1 || true

log_success "AdGuard Home active on port 53 (DNS) and port 3001 (UI at https://adguard.$BASE_DOMAIN)."
