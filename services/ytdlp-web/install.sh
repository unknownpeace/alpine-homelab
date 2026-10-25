#!/bin/sh
set -e
# Service: Media Downloader (yt-dlp Web UI)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring yt-dlp Web Downloader on 127.0.0.1:8080..."

if [ ! -f /usr/bin/yt-dlp ]; then
    curl -fL --retry 3 --progress-bar https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/bin/yt-dlp
    chmod +x /usr/bin/yt-dlp
fi

mkdir -p /opt/ytdlp-web /etc/conf.d
cp -f "$SERVICE_DIR/app.py" /opt/ytdlp-web/app.py
chmod +x /opt/ytdlp-web/app.py

cat << EOF > /etc/conf.d/ytdlp-web
export STORAGE_BASE="${STORAGE_BASE:-/srv/storage}"
EOF

cat << 'EOF' > /etc/init.d/ytdlp-web
#!/sbin/openrc-run
name="ytdlp-web"
description="Simple yt-dlp Web UI"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="/opt/ytdlp-web/app.py"
directory="/opt/ytdlp-web"
output_log="/var/log/ytdlp-web.log"
error_log="/var/log/ytdlp-web.log"
depend() {
    need net
    after firewall
}
EOF
chmod +x /etc/init.d/ytdlp-web
rc-update add ytdlp-web default >/dev/null 2>&1 || true
rc-service ytdlp-web restart >/dev/null 2>&1 || true

deploy_caddy_snippet "ytdlp"

log_success "Media Downloader running on https://ytdl.$BASE_DOMAIN"
