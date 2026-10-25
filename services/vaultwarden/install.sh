#!/bin/sh
set -e
# Service: Vaultwarden (Bitwarden compatible password manager - Hardened & SQLite WAL Tuned)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Vaultwarden on 127.0.0.1:8000 (Domain: https://vault.$BASE_DOMAIN)..."

if ! command -v vaultwarden >/dev/null 2>&1; then
    apk_add vaultwarden vaultwarden-openrc vaultwarden-web-vault sqlite
fi

mkdir -p /var/lib/vaultwarden /etc/homelab
chmod 700 /etc/homelab

# Generate or load ADMIN_TOKEN hash for /admin panel security
ADMIN_HASH=""
if command -v caddy >/dev/null 2>&1; then
    ADMIN_HASH=$(caddy hash-password --plaintext "$AUTH_PASS" 2>/dev/null || true)
fi

# Auto-lock SIGNUPS_ALLOWED if users already exist in Vaultwarden database
SIGNUPS_VAL="true"
if [ -f /var/lib/vaultwarden/db.sqlite3 ] && command -v sqlite3 >/dev/null 2>&1; then
    USER_COUNT=$(sqlite3 /var/lib/vaultwarden/db.sqlite3 "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "0")
    if [ "${USER_COUNT:-0}" -gt 0 ]; then
        SIGNUPS_VAL="false"
        log_info "Existing Vaultwarden account detected ($USER_COUNT users): locking open registration (SIGNUPS_ALLOWED=false)."
    fi
fi

VW_CONN_INIT="PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-16384; PRAGMA temp_store=MEMORY; PRAGMA mmap_size=67108864; PRAGMA busy_timeout=5000; PRAGMA wal_autocheckpoint=1000; PRAGMA foreign_keys=ON;"

cat << EOF > /etc/conf.d/vaultwarden
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
export DATA_FOLDER=/var/lib/vaultwarden
ROCKET_ADDRESS=127.0.0.1
ROCKET_PORT=8000
DOMAIN=https://vault.$BASE_DOMAIN
WEB_VAULT_ENABLED=true
WEB_VAULT_FOLDER=/usr/share/webapps/vaultwarden-web/
SIGNUPS_ALLOWED=$SIGNUPS_VAL
INVITATIONS_ALLOWED=true
SHOW_PASSWORD_HINT=false
DATABASE_URL=sqlite:///var/lib/vaultwarden/db.sqlite3
ENABLE_DB_WAL=true
DATABASE_MAX_CONNS=10
DATABASE_TIMEOUT=30
DATABASE_CONN_INIT="$VW_CONN_INIT"
EOF

cat << EOF > /var/lib/vaultwarden/.env
export DATA_FOLDER=/var/lib/vaultwarden
ROCKET_ADDRESS=127.0.0.1
ROCKET_PORT=8000
DOMAIN=https://vault.$BASE_DOMAIN
WEB_VAULT_ENABLED=true
WEB_VAULT_FOLDER=/usr/share/webapps/vaultwarden-web/
SIGNUPS_ALLOWED=$SIGNUPS_VAL
INVITATIONS_ALLOWED=true
SHOW_PASSWORD_HINT=false
DATABASE_URL=sqlite:///var/lib/vaultwarden/db.sqlite3
ENABLE_DB_WAL=true
DATABASE_MAX_CONNS=10
DATABASE_TIMEOUT=30
DATABASE_CONN_INIT="$VW_CONN_INIT"
EOF

if [ -n "$ADMIN_HASH" ]; then
    printf "ADMIN_TOKEN='%s'\n" "$ADMIN_HASH" >> /etc/conf.d/vaultwarden
    printf "ADMIN_TOKEN='%s'\n" "$ADMIN_HASH" >> /var/lib/vaultwarden/.env
fi

# Apply persistent WAL & PRAGMA optimization on SQLite database file
if [ -f /var/lib/vaultwarden/db.sqlite3 ]; then
    sqlite3 /var/lib/vaultwarden/db.sqlite3 "$VW_CONN_INIT PRAGMA optimize;" >/dev/null 2>&1 || true
fi

chown -R vaultwarden:vaultwarden /var/lib/vaultwarden
chmod 600 /var/lib/vaultwarden/.env /etc/conf.d/vaultwarden 2>/dev/null || true
rc-update add vaultwarden default >/dev/null 2>&1 || true
rc-service vaultwarden restart >/dev/null 2>&1 || true

# Ensure WAL is applied even if DB was initialized on first start
for _ in 1 2 3; do
    if [ -f /var/lib/vaultwarden/db.sqlite3 ]; then
        sqlite3 /var/lib/vaultwarden/db.sqlite3 "$VW_CONN_INIT" >/dev/null 2>&1 || true
        chown -R vaultwarden:vaultwarden /var/lib/vaultwarden
        break
    fi
    sleep 1
done

deploy_caddy_snippet "vaultwarden"

log_success "Vaultwarden running on https://vault.$BASE_DOMAIN (SQLite WAL + synchronous=NORMAL active)"
