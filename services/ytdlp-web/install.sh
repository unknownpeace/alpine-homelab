#!/bin/sh
set -e
# Service: Media Downloader (yt-dlp Web UI - Unprivileged Daemon)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring unprivileged yt-dlp Web Downloader on 127.0.0.1:8080..."

if [ ! -f /usr/bin/yt-dlp ]; then
    curl -fL --retry 3 --progress-bar https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/bin/yt-dlp
    chmod +x /usr/bin/yt-dlp
fi

addgroup -g 1000 media 2>/dev/null || addgroup media 2>/dev/null || true
if ! id -u ytdlp >/dev/null 2>&1; then
    adduser -S -D -H -G media ytdlp 2>/dev/null || true
fi
addgroup ytdlp media 2>/dev/null || true

mkdir -p /opt/ytdlp-web /etc/conf.d
cp -f "$SERVICE_DIR/app.py" /opt/ytdlp-web/app.py
chmod +x /opt/ytdlp-web/app.py
chown -R ytdlp:media /opt/ytdlp-web

touch /var/log/ytdlp-web.log
chown ytdlp:media /var/log/ytdlp-web.log

cat << EOF > /etc/conf.d/ytdlp-web
export STORAGE_BASE="${STORAGE_BASE:-/srv/storage}"
EOF

cat << 'EOF' > /etc/init.d/ytdlp-web
#!/sbin/openrc-run
name="ytdlp-web"
description="Simple yt-dlp Web UI (Unprivileged)"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="/opt/ytdlp-web/app.py"
command_user="ytdlp:media"
directory="/opt/ytdlp-web"
output_log="/var/log/ytdlp-web.log"
error_log="/var/log/ytdlp-web.log"
respawn_delay=2
respawn_max=5

depend() {
    need net
    after firewall
}
EOF
chmod +x /etc/init.d/ytdlp-web
rc-update add ytdlp-web default >/dev/null 2>&1 || true
rc-service ytdlp-web restart >/dev/null 2>&1 || true

deploy_caddy_snippet "ytdlp"

log_success "Media Downloader running as ytdlp:media on https://ytdl.$BASE_DOMAIN"
