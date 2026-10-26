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
    apk_add forgejo forgejo-openrc sqlite git-lfs python3
fi

addgroup forgejo media 2>/dev/null || true

mkdir -p /etc/forgejo /var/lib/forgejo/data/lfs /var/log/forgejo /etc/conf.d /usr/local/bin
chown -R forgejo:forgejo /var/log/forgejo /var/lib/forgejo 2>/dev/null || true

# Deploy zero-dependency Jupyter notebook renderer for Forgejo web UI
cat << 'EOF' > /usr/local/bin/ipynb-render
#!/usr/bin/env python3
"""Zero-dependency Jupyter Notebook (.ipynb) to HTML renderer for Forgejo."""
import sys, json, html

def render_notebook():
    try:
        nb = json.load(sys.stdin)
    except Exception as exc:
        print(f"<pre>Error parsing notebook: {html.escape(str(exc))}</pre>")
        return

    out = ['<div class="jupyter-notebook" style="font-family:sans-serif;line-height:1.5;">']
    for idx, cell in enumerate(nb.get("cells", []), 1):
        ctype = cell.get("cell_type", "")
        source = "".join(cell.get("source", []))
        if ctype == "markdown":
            out.append(f'<div style="margin:12px 0;padding:8px;border-left:3px solid #1a73e8;">'
                       f'<pre style="white-space:pre-wrap;font-family:inherit;margin:0;">{html.escape(source)}</pre></div>')
        elif ctype == "code":
            exec_count = cell.get("execution_count") or idx
            out.append(f'<div style="margin:12px 0;background:#1e1e2e;color:#cdd6f4;padding:10px;border-radius:6px;">'
                       f'<div style="font-size:0.75em;color:#89b4fa;margin-bottom:4px;">In [{exec_count}]:</div>'
                       f'<pre style="margin:0;overflow-x:auto;"><code>{html.escape(source)}</code></pre></div>')
            for output in cell.get("outputs", []):
                text = "".join(output.get("text", output.get("data", {}).get("text/plain", [])))
                if text:
                    out.append(f'<pre style="margin:4px 0 12px 12px;padding:8px;background:#f4f4f8;'
                               f'color:#111;border-radius:4px;overflow-x:auto;">{html.escape(text)}</pre>')
    out.append("</div>")
    sys.stdout.write("\n".join(out))

if __name__ == "__main__":
    render_notebook()
EOF
chmod 755 /usr/local/bin/ipynb-render

cat << 'EOF' > /etc/conf.d/forgejo
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
EOF

LFS_SECRET=$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 32 || echo "HomelabLFSSecretKey2026Secure0123")

cat << EOF > /etc/forgejo/app.ini
APP_NAME = Homelab Git
RUN_USER = forgejo
RUN_MODE = prod
WORK_PATH = /var/lib/forgejo

[repository]
SCRIPT_TYPE = sh

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
APP_DATA_PATH = /var/lib/forgejo/data
ENABLE_GZIP = false
STATIC_CACHE_TIME = 168h
OFFLINE_MODE = true

# Integrated hardened SSH Server on port 2222
DISABLE_SSH = false
START_SSH_SERVER = true
SSH_DOMAIN = git.$BASE_DOMAIN
SSH_PORT = 2222
SSH_LISTEN_HOST = 0.0.0.0
SSH_LISTEN_PORT = 2222
SSH_SERVER_CIPHERS = chacha20-poly1305@openssh.com,aes128-gcm@openssh.com,aes256-gcm@openssh.com
SSH_SERVER_KEY_EXCHANGES = curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256

# Git LFS Support
LFS_START_SERVER = true
LFS_JWT_SECRET = $LFS_SECRET
LFS_MAX_FILE_SIZE = 536870912

[lfs]
STORAGE_TYPE = local
PATH = /var/lib/forgejo/data/lfs

[database]
DB_TYPE = sqlite3
PATH = /var/lib/forgejo/data/forgejo.db?_pragma=synchronous(NORMAL)&_pragma=cache_size(-32768)&_pragma=temp_store(MEMORY)&_pragma=mmap_size(134217728)&_pragma=busy_timeout(5000)
SQLITE_JOURNAL_MODE = WAL
SQLITE_TIMEOUT = 5000
MAX_OPEN_CONNS = 16
MAX_IDLE_CONNS = 4

[git]
MAX_GIT_DIFF_LINES = 500
MAX_GIT_DIFF_LINE_CHARACTERS = 2500
MAX_GIT_DIFF_FILES = 50
GC_ARGS = --window=10 --depth=20
ENABLE_AUTO_GIT_WIRE_PROTOCOL = true

[queue.push_update]
TYPE = channel
BATCH_LENGTH = 20
MAX_WORKERS = 2

[security]
INSTALL_LOCK = true
REVERSE_PROXY_LIMIT = 1
REVERSE_PROXY_TRUSTED_PROXIES = 127.0.0.1/8,::1/128
DISABLE_GIT_HOOKS = true

[service]
DISABLE_REGISTRATION = true
REQUIRE_SIGNIN_VIEW = true

[migrations]
ALLOW_LOCALNETWORKS = false

[markup.jupyter]
ENABLED = true
FILE_EXTENSIONS = .ipynb
RENDER_COMMAND = "/usr/local/bin/ipynb-render"
IS_INPUT_FILE = false

[markup.sanitizer.jupyter]
ELEMENT = div,pre,code,span
ALLOW_ATTR = style,class
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
