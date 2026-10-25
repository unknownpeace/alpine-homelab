#!/bin/sh
set -e
# Service: Forgejo Git Server (SQLite WAL & Low-Memory Tuned)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Forgejo Git on 127.0.0.1:3000 (Domain: https://git.$BASE_DOMAIN, SSH: 2222)..."

if ! command -v forgejo >/dev/null 2>&1; then
    apk_add forgejo forgejo-openrc sqlite
fi

addgroup forgejo media 2>/dev/null || true

mkdir -p /etc/forgejo /var/lib/forgejo/data /var/log/forgejo /etc/conf.d
chown -R forgejo:forgejo /var/log/forgejo 2>/dev/null || true

cat << 'EOF' > /etc/conf.d/forgejo
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
EOF

cat << EOF > /etc/forgejo/app.ini
APP_NAME = Homelab Git
RUN_USER = forgejo
RUN_MODE = prod

[log]
MODE = file
LEVEL = Info
ROOT_PATH = /var/log/forgejo

[log.file]
FILE_NAME = forgejo.log

[server]
DOMAIN = git.$BASE_DOMAIN
HTTP_ADDR = 127.0.0.1
HTTP_PORT = 3000
ROOT_URL = https://git.$BASE_DOMAIN/
DISABLE_SSH = false
SSH_PORT = 2222
SSH_LISTEN_PORT = 2222
START_SSH_SERVER = true
APP_DATA_PATH = /var/lib/forgejo/data

[database]
DB_TYPE = sqlite3
PATH = /var/lib/forgejo/data/forgejo.db?_pragma=synchronous(NORMAL)&_pragma=cache_size(-32768)&_pragma=temp_store(MEMORY)&_pragma=mmap_size(134217728)&_pragma=busy_timeout(5000)
SQLITE_JOURNAL_MODE = WAL
SQLITE_TIMEOUT = 5000
MAX_OPEN_CONNS = 16
MAX_IDLE_CONNS = 4

[indexer]
ISSUE_INDEXER_TYPE = bleve
REPO_INDEXER_ENABLED = false

[security]
INSTALL_LOCK = true
EOF

chown -R forgejo:www-data /etc/forgejo /var/lib/forgejo
rc-update add forgejo default >/dev/null 2>&1 || true
rc-service forgejo restart >/dev/null 2>&1 || true

# Provision primary administrator account & apply persistent SQLite WAL pragmas
log_info "Ensuring initial Forgejo administrator account ($AUTH_USER) and SQLite WAL tuning..."
for _ in 1 2 3 4; do
    if [ -f /var/lib/forgejo/data/forgejo.db ]; then
        sqlite3 /var/lib/forgejo/data/forgejo.db \
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA cache_size=-32768; PRAGMA temp_store=MEMORY; PRAGMA mmap_size=134217728; PRAGMA busy_timeout=5000; PRAGMA wal_autocheckpoint=1000; PRAGMA optimize;" >/dev/null 2>&1 || true
        chown -R forgejo:www-data /var/lib/forgejo/data
        su -s /bin/sh forgejo -c "forgejo admin user create --admin --username '$AUTH_USER' --password '$AUTH_PASS' --email '$AUTH_USER@$BASE_DOMAIN' --must-change-password=false" >/dev/null 2>&1 || true
        break
    fi
    sleep 1
done

deploy_caddy_snippet "forgejo"

log_success "Forgejo Git running on https://git.$BASE_DOMAIN (SQLite WAL + 32MB cache tuned)"
