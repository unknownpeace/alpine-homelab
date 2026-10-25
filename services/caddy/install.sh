#!/bin/sh
set -e
# Service: Caddy 2 Reverse Proxy & Local PKI CA

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying Caddy 2 Reverse Proxy and Local PKI CA (Base domain: *.$BASE_DOMAIN)..."

if ! command -v caddy >/dev/null 2>&1; then
    apk_add caddy caddy-openrc
fi

setcap cap_net_bind_service=+ep /usr/sbin/caddy 2>/dev/null || true

RAW_HASH=$(caddy hash-password --plaintext "$AUTH_PASS")

cat << EOF > /etc/caddy/Caddyfile
{
    local_certs
    skip_install_trust
}

(homelab_auth) {
    basic_auth {
        $AUTH_USER $RAW_HASH
    }
}

homelab.$BASE_DOMAIN, $IP_HOST {
    root * /var/www/dashboard
    file_server
}
EOF

# Conditionally configure reverse proxies based on ENABLE_* flags
if [ "${ENABLE_VAULTWARDEN:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

vault.$BASE_DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
EOF
fi

if [ "${ENABLE_FORGEJO:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

git.$BASE_DOMAIN {
    reverse_proxy 127.0.0.1:3000
}
EOF
fi

if [ "${ENABLE_CONDUIT:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

matrix.$BASE_DOMAIN {
    reverse_proxy 127.0.0.1:6167
}
EOF
fi

if [ "${ENABLE_SINGBOX:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

proxy.$BASE_DOMAIN {
    import homelab_auth
    redir / /ui/
    reverse_proxy 127.0.0.1:9090
}
EOF
fi

if [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

lxc.$BASE_DOMAIN {
    import homelab_auth
    handle /ttyd* {
        reverse_proxy 127.0.0.1:7681
    }
    handle {
        reverse_proxy 127.0.0.1:8888
    }
}
EOF
fi

if [ "${ENABLE_NAVIDROME:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

music.$BASE_DOMAIN {
    reverse_proxy 127.0.0.1:4533
}
EOF
fi

if [ "${ENABLE_TRANSMISSION:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

torrent.$BASE_DOMAIN {
    import homelab_auth
    redir / /transmission/web/
    reverse_proxy 127.0.0.1:9091
}
EOF
fi

if [ "${ENABLE_YTDLP:-yes}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

ytdl.$BASE_DOMAIN {
    import homelab_auth
    reverse_proxy 127.0.0.1:8080
}
EOF
fi

if [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ]; then
cat << EOF >> /etc/caddy/Caddyfile

adguard.$BASE_DOMAIN {
    reverse_proxy 127.0.0.1:3001
}
EOF
fi

rc-update add caddy default >/dev/null 2>&1 || true
rc-service caddy restart >/dev/null 2>&1 || true

# Export Root CA Certificate
CADDY_ROOT_CERT="/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"
for attempt in 1 2 3; do
    if [ -f "$CADDY_ROOT_CERT" ]; then
        cp "$CADDY_ROOT_CERT" /root/caddy-homelab-root.crt
        mkdir -p /var/www/dashboard
        cp "$CADDY_ROOT_CERT" /var/www/dashboard/caddy-homelab-root.crt 2>/dev/null || true
        log_success "Caddy Root CA exported to /root/caddy-homelab-root.crt and dashboard"
        break
    fi
    sleep 1
done

log_success "Caddy 2 configured with HTTPS on *.$BASE_DOMAIN domains."
