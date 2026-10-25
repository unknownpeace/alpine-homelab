#!/bin/sh
set -e
# Service: LXC Sandbox Subsystem & Web Manager

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying LXC Sandbox Subsystem and Web Manager (Bottle WSGI + ttyd)..."

mkdir -p /opt/lxc-manager /var/lib/lxc/cache

# 1. Pre-cache Alpine minirootfs for instant 1s sandbox cloning
if [ ! -f /var/lib/lxc/cache/alpine-minirootfs.tar.gz ]; then
    log_info "Caching Alpine minirootfs template for instant LXC sandbox provisioning..."
    curl -fL --retry 3 --connect-timeout 8 "$MINIROOTFS_URL" -o /var/lib/lxc/cache/alpine-minirootfs.tar.gz 2>/dev/null || true
fi

# 2. Deploy app.py and term-attach.sh
cp -f "$SERVICE_DIR/app.py" /opt/lxc-manager/app.py
chmod +x /opt/lxc-manager/app.py

cp -f "$SERVICE_DIR/term-attach.sh" /opt/lxc-manager/term-attach.sh
chmod +x /opt/lxc-manager/term-attach.sh

# 3. OpenRC service for ttyd
cat << 'EOF' > /etc/init.d/ttyd
#!/sbin/openrc-run
name="ttyd"
description="Interactive Web Terminal for LXC Containers"
supervisor="supervise-daemon"
command="/usr/bin/ttyd"
command_args="-b /ttyd -p 7681 -i 127.0.0.1 -W -a /opt/lxc-manager/term-attach.sh"
command_user="root"
directory="/opt/lxc-manager"
respawn_delay=2
respawn_max=5

depend() {
    need net lxc
    after caddy lxc-manager
}
EOF
chmod +x /etc/init.d/ttyd
rc-update add ttyd default >/dev/null 2>&1 || true
rc-service ttyd restart >/dev/null 2>&1 || true

# 4. OpenRC service for lxc-manager
cat << 'EOF' > /etc/init.d/lxc-manager
#!/sbin/openrc-run
name="lxc-manager"
description="LXC Sandbox Web Manager"
supervisor="supervise-daemon"
command="/usr/bin/python3"
command_args="/opt/lxc-manager/app.py"
directory="/opt/lxc-manager"
respawn_delay=2
respawn_max=5

depend() {
    need net lxc
    after firewall caddy
}
EOF
chmod +x /etc/init.d/lxc-manager
rc-update add lxc-manager default >/dev/null 2>&1 || true
rc-service lxc-manager restart >/dev/null 2>&1 || true

log_success "LXC Sandbox Manager and ttyd Web Terminal deployed on https://lxc.$BASE_DOMAIN"
