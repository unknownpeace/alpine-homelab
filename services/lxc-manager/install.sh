#!/bin/sh
set -e
# Service: LXC Sandbox Subsystem & Web Manager

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Deploying LXC Sandbox Subsystem and Web Manager (Bottle WSGI + ttyd)..."

if ! command -v lxc-create >/dev/null 2>&1 || ! command -v ttyd >/dev/null 2>&1; then
    apk_add lxc lxc-openrc lxc-templates lxc-download rsync ttyd
fi

# Setup LXC Sandbox NAT bridge (10.0.3.1/24)
if ! grep -q 'lxcbr0' /etc/network/interfaces; then
cat << 'EOF' >> /etc/network/interfaces

auto lxcbr0
iface lxcbr0 inet static
    address 10.0.3.1
    netmask 255.255.255.0
    bridge-stp 0
EOF
fi
ifup lxcbr0 2>/dev/null || true

# Enable and start OpenRC cgroups and lxc services
rc-update add cgroups sysinit >/dev/null 2>&1 || true
rc-service cgroups start >/dev/null 2>&1 || true
rc-update add lxc default >/dev/null 2>&1 || true
rc-service lxc start >/dev/null 2>&1 || true

# 1. Pre-cache Alpine minirootfs for instant 1s sandbox cloning
mkdir -p /var/lib/lxc/cache /opt/lxc-manager
if [ ! -s /var/lib/lxc/cache/alpine-minirootfs.tar.gz ]; then
    log_info "Caching Alpine minirootfs template for instant LXC sandbox provisioning..."
    curl -fL --retry 3 --connect-timeout 15 "$MINIROOTFS_URL" -o /var/lib/lxc/cache/alpine-minirootfs.tar.gz || rm -f /var/lib/lxc/cache/alpine-minirootfs.tar.gz
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
output_log="/var/log/lxc-manager.log"
error_log="/var/log/lxc-manager.err"
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

deploy_caddy_snippet "lxc-manager"

log_success "LXC Sandbox Manager and ttyd Web Terminal deployed on https://lxc.$BASE_DOMAIN"
