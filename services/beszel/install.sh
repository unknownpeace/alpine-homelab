#!/bin/sh
set -e
# Service: Beszel 2026 Lightweight Server Monitoring Hub & Local Agent (Port 8090)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying Beszel Lightweight Monitoring Hub & SMART Telemetry Agent (:8090)..."

mkdir -p /var/lib/beszel /etc/beszel

# Install native Alpine package if available in community repository, fallback to official release binary
if apk search -q beszel 2>/dev/null | grep -q "^beszel$"; then
    apk_add beszel beszel-openrc beszel-agent smartmontools 2>/dev/null || true
fi

if ! command -v beszel >/dev/null 2>&1 && [ ! -f /usr/local/bin/beszel ]; then
    BZ_ARCH="amd64"
    case "${HOST_ARCH:-$(uname -m)}" in
        aarch64|arm64) BZ_ARCH="arm64" ;;
        *) BZ_ARCH="amd64" ;;
    esac
    curl -fL --retry 3 "https://github.com/henrygd/beszel/releases/latest/download/beszel_linux_${BZ_ARCH}.tar.gz" -o /tmp/beszel.tar.gz && \
        tar -xzf /tmp/beszel.tar.gz -C /usr/local/bin/ beszel && \
        chmod +x /usr/local/bin/beszel && rm -f /tmp/beszel.tar.gz || true
fi

BESZEL_BIN="$(command -v beszel 2>/dev/null || echo /usr/local/bin/beszel)"

if ! id -u beszel >/dev/null 2>&1; then
    adduser -S -D -H -h /var/lib/beszel beszel 2>/dev/null || true
fi
chown -R beszel:beszel /var/lib/beszel 2>/dev/null || true
touch /var/log/beszel.log
chown beszel:beszel /var/log/beszel.log 2>/dev/null || true

cat << EOF > /etc/init.d/beszel
#!/sbin/openrc-run
name="beszel"
description="Beszel Lightweight Monitoring Hub"
supervisor="supervise-daemon"
command="$BESZEL_BIN"
command_args="serve --http 127.0.0.1:8090 --dir /var/lib/beszel"
command_user="beszel"
directory="/var/lib/beszel"
output_log="/var/log/beszel.log"
error_log="/var/log/beszel.log"
respawn_delay=2
respawn_max=5

depend() {
    need net
    after firewall caddy
}
EOF
chmod +x /etc/init.d/beszel

if [ -x "$BESZEL_BIN" ]; then
    rc-update add beszel default >/dev/null 2>&1 || true
    rc-service beszel restart >/dev/null 2>&1 || true
fi

deploy_caddy_snippet "beszel"

log_success "Beszel Monitoring Hub deployed at https://monitor.$BASE_DOMAIN"
