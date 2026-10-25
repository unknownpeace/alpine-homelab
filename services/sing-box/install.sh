#!/bin/sh
set -e
# Service: sing-box-extended 1.14, DNS Engine (Port 53) & Metacubexd Web UI

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying sing-box-extended 1.14 and DNS Engine..."

# 1. Download sing-box-extended binary if missing
if [ ! -f /usr/bin/sing-box ]; then
    log_info "Downloading sing-box-extended binary (35 MB)..."
    curl -fL --retry 3 --progress-bar https://github.com/shtorm-7/sing-box-extended/releases/download/v1.14.3-extended-2.7.3/sing-box-1.14.3-extended-2.7.3-linux-amd64.tar.gz -o /tmp/sing-box.tar.gz
    tar -xzf /tmp/sing-box.tar.gz -C /tmp/
    cp /tmp/sing-box-*/sing-box /usr/bin/sing-box
    chmod +x /usr/bin/sing-box
    rm -rf /tmp/sing-box*
fi

# 2. Metacubexd Web UI
if [ ! -d /var/lib/sing-box/ui ] || [ ! -f /var/lib/sing-box/ui/index.html ]; then
    log_info "Downloading Metacubexd Web UI (2.4 MB)..."
    mkdir -p /var/lib/sing-box/ui
    curl -fL --retry 3 --progress-bar https://github.com/MetaCubeX/metacubexd/releases/download/v1.273.1/compressed-dist.tgz -o /tmp/metacubexd.tgz
    tar -xzf /tmp/metacubexd.tgz -C /var/lib/sing-box/ui
    rm -f /tmp/metacubexd.tgz

    cat << 'EOF' > /var/lib/sing-box/ui/config.js
window.__METACUBEXD_CONFIG__ = {
  defaultBackendURL: '',
  githubToken: '',
}
EOF
fi

# 3. Rule-sets
chmod +x "$SERVICE_DIR/update-rules.sh"
"$SERVICE_DIR/update-rules.sh"

# 4. OpenRC init script
cat << 'EOF' > /etc/init.d/sing-box
#!/sbin/openrc-run
command="/usr/bin/sing-box"
command_args="run -c /etc/sing-box/config.json"
command_background=true
pidfile="/run/sing-box.pid"
depend() {
    need net
    after firewall
}
EOF
chmod +x /etc/init.d/sing-box

# 5. Copy update-sub.py
mkdir -p /usr/local/bin /etc/sing-box
cp -f "$SERVICE_DIR/update-sub.py" /usr/local/bin/update-sub.py
chmod +x /usr/local/bin/update-sub.py

# 6. Generate configuration and start
SINGBOX_DNS_PORT="53"
if [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ]; then
    SINGBOX_DNS_PORT="5353"
    log_info "AdGuard Home enabled: assigning sing-box DNS to internal port 5353..."
fi

log_info "Compiling sing-box routing tables and DNS mapping (*.$BASE_DOMAIN)..."
IP_HOST="$IP_HOST" BASE_DOMAIN="$BASE_DOMAIN" DNS_UPSTREAM_PRIMARY="$DNS_UPSTREAM_PRIMARY" DNS_UPSTREAM_SECONDARY="$DNS_UPSTREAM_SECONDARY" SINGBOX_DNS_PORT="$SINGBOX_DNS_PORT" /usr/local/bin/update-sub.py "$SUB_URL"

rc-update add sing-box default >/dev/null 2>&1 || true
rc-service sing-box restart >/dev/null 2>&1 || true

# 7. Configure host DNS resolution to point to local sing-box
cat << EOF > /etc/resolv.conf
nameserver 127.0.0.1
nameserver 1.1.1.1
nameserver 77.88.8.8
EOF

log_success "sing-box DNS and routing engine initialized (Domain: *.$BASE_DOMAIN)."
