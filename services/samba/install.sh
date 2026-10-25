#!/bin/sh
set -e
# Service: Samba & WSDD (Windows Network Shares & Discovery)

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SERVICE_DIR/../.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring Samba and WSDD Network Discovery..."

if ! command -v smbd >/dev/null 2>&1; then
    apk_add samba-server samba-server-openrc samba-common-tools wsdd wsdd-openrc
fi

(echo "$AUTH_PASS"; echo "$AUTH_PASS") | smbpasswd -a -s "$AUTH_USER" 2>/dev/null || true
smbpasswd -e "$AUTH_USER" 2>/dev/null || true

cat << EOF > /etc/samba/smb.conf
[global]
    workgroup = WORKGROUP
    netbios name = HOMELAB
    server string = Homelab Baremetal Appliance
    server role = standalone server
    security = user
    map to guest = Never
    dns proxy = no
    log file = /var/log/samba/%m.log
    max log size = 50
    load printers = no
    printing = bsd
    printcap name = /dev/null
    disable spoolss = yes
    server min protocol = SMB2_10
    server max protocol = SMB3
    server signing = auto
    smb encrypt = auto
    ea support = yes
    vfs objects = streams_xattr

[Storage]
    path = $STORAGE_BASE
    browseable = yes
    writable = yes
    valid users = $AUTH_USER
    force user = $AUTH_USER
    force group = media
    read only = no
    force create mode = 0664
    force directory mode = 2775

[Music]
    path = $STORAGE_BASE/music
    browseable = yes
    writable = yes
    valid users = $AUTH_USER
    force user = $AUTH_USER
    force group = media
    read only = no
    force create mode = 0664
    force directory mode = 2775

[Downloads]
    path = $STORAGE_BASE/downloads
    browseable = yes
    writable = yes
    valid users = $AUTH_USER
    force user = $AUTH_USER
    force group = media
    read only = no
    force create mode = 0664
    force directory mode = 2775
EOF

rc-update add samba default >/dev/null 2>&1 || true
rc-service samba restart >/dev/null 2>&1 || true

wsdd_if="${PHYSICAL_IF:-br0}"
[ -d /sys/class/net/br0 ] && wsdd_if="br0"
echo "WSDD_OPTS=\"-i $wsdd_if -n HOMELAB -w WORKGROUP -p -4\"" > /etc/conf.d/wsdd
rc-update add wsdd default >/dev/null 2>&1 || true
rc-service wsdd restart >/dev/null 2>&1 || true

log_success "Samba shares available at \\\\$IP_HOST\\Storage and \\\\HOMELAB\\Storage"
