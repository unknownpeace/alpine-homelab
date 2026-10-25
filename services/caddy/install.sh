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
    servers {
        protocols h1 h2 h3
    }
    local_certs
    skip_install_trust
    log {
        output file /var/log/caddy/caddy.log {
            roll_size 10mb
            roll_keep 5
        }
        format json
        level INFO
    }
}

(security_headers) {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        Permissions-Policy "accelerometer=(), camera=(), geolocation=(), microphone=(), payment=(), usb=()"
        -Server
    }
}

(access_log) {
    log {
        output file /var/log/caddy/access.log {
            roll_size 10mb
            roll_keep 3
        }
        format json
    }
}

(common_optimizations) {
    import security_headers
    import access_log
    encode zstd gzip
    handle_errors {
        @custom_err expression `{err.status_code} in [404, 500, 502, 503, 504]`
        handle @custom_err {
            header Content-Type "text/html; charset=utf-8"
            header Cache-Control "no-store"
            respond `<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{err.status_code} — {err.status_text}</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #0f111a; color: #e2e8f0; font-family: system-ui, -apple-system, sans-serif; }
  .card { max-width: 460px; width: 90%; padding: 32px; background: #181b28;
          border: 1px solid #2d334a; border-radius: 14px; box-shadow: 0 20px 40px rgba(0,0,0,0.45); }
  .badge { display: inline-block; padding: 4px 12px; border-radius: 999px; background: rgba(239,68,68,0.15);
           color: #f87171; font-weight: 700; font-size: 0.85rem; letter-spacing: 0.05em; }
  h1 { margin: 16px 0 8px; font-size: 1.75rem; color: #ffffff; }
  p { margin: 0 0 20px; color: #94a3b8; line-height: 1.6; font-size: 0.95rem; }
  .meta { padding: 12px; background: #0f111a; border-radius: 8px; font-family: monospace;
          font-size: 0.8rem; color: #64748b; overflow-x: auto; margin-bottom: 20px; }
  .btn { display: inline-block; padding: 10px 18px; background: #2563eb; color: #fff;
         text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 0.9rem; }
  .btn:hover { background: #1d4ed8; }
</style>
</head>
<body>
  <div class="card">
    <span class="badge">HTTP {err.status_code}</span>
    <h1>{err.status_text}</h1>
    <p>Сервис временно перезапускается супервизором OpenRC или запрошенный ресурс не существует на узле.</p>
    <div class="meta">Host: {http.request.host}<br>URI: {http.request.uri}</div>
    <a class="btn" href="javascript:location.reload()">Повторить запрос</a>
  </div>
</body>
</html>` {err.status_code}
        }
    }
}

(homelab_auth) {
    basic_auth {
        $AUTH_USER $RAW_HASH
    }
}

homelab.$BASE_DOMAIN, $IP_HOST {
    import common_optimizations
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
