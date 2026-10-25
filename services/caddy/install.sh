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

addgroup caddy media 2>/dev/null || true
setcap cap_net_bind_service=+ep /usr/sbin/caddy 2>/dev/null || true

RAW_HASH=$(caddy hash-password --plaintext "$AUTH_PASS")

mkdir -p /etc/caddy/conf.d /var/log/caddy
chown -R caddy:caddy /var/log/caddy 2>/dev/null || true
rm -f /etc/caddy/conf.d/*.conf

cat << EOF > /etc/caddy/Caddyfile
{
    local_certs
    skip_install_trust
    log {
        output file /var/log/caddy/caddy.log {
            roll_size 10mb
            roll_keep 5
        }
        level INFO
    }
}

(security_headers) {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }
}

(homelab_auth) {
    basic_auth {
        $AUTH_USER $RAW_HASH
    }
}

homelab.$BASE_DOMAIN, $IP_HOST {
    import security_headers
    handle /api/proxy/update {
        import homelab_auth
        reverse_proxy 127.0.0.1:8888
    }
    handle /api/proxy/refresh {
        import homelab_auth
        reverse_proxy 127.0.0.1:8888
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:8888
    }
    handle {
        root * /var/www/dashboard
        file_server
    }
}

import /etc/caddy/conf.d/*.conf
EOF

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
