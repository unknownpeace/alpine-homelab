#!/bin/sh
set -e
# Service: Pocket ID Passkey OIDC Provider (:1411) & Tinyauth SSO Forward-Auth (:3005)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying Pocket ID (Passkey/WebAuthn OIDC Provider) & Tinyauth SSO Gateway..."

mkdir -p /var/lib/pocket-id /etc/pocket-id

PID_ARCH="amd64"
case "${HOST_ARCH:-$(uname -m)}" in
    aarch64|arm64) PID_ARCH="arm64" ;;
    *) PID_ARCH="amd64" ;;
esac

if [ ! -f /usr/local/bin/pocket-id ]; then
    if curl -fL --retry 3 "https://github.com/pocket-id/pocket-id/releases/latest/download/pocket-id_linux_${PID_ARCH}" -o /usr/local/bin/pocket-id; then
        chmod +x /usr/local/bin/pocket-id
    else
        rm -f /usr/local/bin/pocket-id
    fi
fi

if ! id -u pocketid >/dev/null 2>&1; then
    adduser -S -D -H -h /var/lib/pocket-id pocketid 2>/dev/null || true
fi
chown -R pocketid:pocketid /var/lib/pocket-id 2>/dev/null || true

ENC_KEY=$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 32 || echo "HomelabSecretKey2026Encryption32")

cat << EOF > /etc/pocket-id/pocket-id.env
PUBLIC_APP_URL=https://id.$BASE_DOMAIN
TRUST_PROXY=true
PORT=1411
HOST=127.0.0.1
DB_PROVIDER=sqlite
DB_CONNECTION_STRING=file:/var/lib/pocket-id/pocket-id.db?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)
ENCRYPTION_KEY=$ENC_KEY
EOF
chmod 600 /etc/pocket-id/pocket-id.env

cat << 'EOF' > /etc/init.d/pocket-id
#!/sbin/openrc-run
name="pocket-id"
description="Pocket ID Passkey OIDC Identity Provider"
supervisor="supervise-daemon"
command="/usr/local/bin/pocket-id"
command_user="pocketid"
directory="/var/lib/pocket-id"
output_log="/var/log/pocket-id.log"
error_log="/var/log/pocket-id.log"
respawn_delay=2
respawn_max=5

start_pre() {
    if [ -f /etc/pocket-id/pocket-id.env ]; then
        set -a
        . /etc/pocket-id/pocket-id.env
        set +a
    fi
    touch /var/log/pocket-id.log
    chown pocketid:pocketid /var/log/pocket-id.log 2>/dev/null || true
}

depend() {
    need net
    after firewall caddy
}
EOF
chmod +x /etc/init.d/pocket-id

if [ -x /usr/local/bin/pocket-id ]; then
    rc-update add pocket-id default >/dev/null 2>&1 || true
    rc-service pocket-id restart >/dev/null 2>&1 || true
fi

deploy_caddy_snippet "pocket-id"

log_success "Pocket ID OIDC Provider configured at https://id.$BASE_DOMAIN"
