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

mkdir -p "$STORAGE_BASE/timemachine"
chown -R "${SAFE_USER:-admin}:media" "$STORAGE_BASE/timemachine" 2>/dev/null || true
chmod 2770 "$STORAGE_BASE/timemachine" 2>/dev/null || true

cat << EOF > /etc/samba/smb.conf
[global]
    workgroup = WORKGROUP
    netbios name = HOMELAB
    server string = Alpine Homelab NAS (SMB3 & Apple TimeMachine)
    server role = standalone server
    security = user
    map to guest = Bad User
    dns proxy = no
    log file = /var/log/samba/%m.log
    max log size = 50
    load printers = no
    printing = bsd
    printcap name = /dev/null
    disable spoolss = yes

    # SMB3 & Multi-Channel performance tuning (Windows 10/11 & macOS)
    server min protocol = SMB3_00
    server multi channel support = yes
    smb2 leases = yes
    oplocks = yes
    kernel oplocks = no
    strict locking = no
    posix locking = no

    # Zero-copy sendfile & async I/O
    use sendfile = yes
    aio read size = 1
    aio write size = 1
    min receivefile size = 16384
    ea support = yes

    # Apple macOS VFS enhancements
    vfs objects = catia fruit streams_xattr
    fruit:aapl = yes
    fruit:model = MacSamba
    fruit:metadata = stream
    fruit:resource = file
    fruit:zero_file_id = yes
    fruit:nfs_aces = no
    fruit:copyfile = yes
    fruit:wipe_intentionally_left_blank_rfork = yes
    fruit:delete_empty_adfiles = yes
    fruit:veto_appledouble = no
    readdir_attr:aapl_rsize = yes
    readdir_attr:aapl_finder_info = yes
    readdir_attr:aapl_max_access = no
    spotlight backend = noindex

    # Veto unwanted junk files
    veto files = /Thumbs.db/.DS_Store/desktop.ini/
    delete veto files = yes

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

[TimeMachine]
    comment = Apple macOS Time Machine Backup
    path = $STORAGE_BASE/timemachine
    browseable = yes
    writable = yes
    valid users = $AUTH_USER
    force user = $AUTH_USER
    force group = media
    read only = no
    create mask = 0600
    directory mask = 0700
    vfs objects = catia fruit streams_xattr
    fruit:time machine = yes
    fruit:time machine max size = 500G
    dfree command =
EOF

rc-update add samba default >/dev/null 2>&1 || true
rc-service samba restart >/dev/null 2>&1 || true

wsdd_if="${PHYSICAL_IF:-br0}"
[ -d /sys/class/net/br0 ] && wsdd_if="br0"
echo "WSDD_OPTS=\"-i $wsdd_if -n HOMELAB -w WORKGROUP -p -4\"" > /etc/conf.d/wsdd
rc-update add wsdd default >/dev/null 2>&1 || true
rc-service wsdd restart >/dev/null 2>&1 || true

log_success "Samba shares available at \\\\$IP_HOST\\Storage and \\\\HOMELAB\\Storage"
