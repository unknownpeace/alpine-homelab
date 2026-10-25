#!/bin/sh
set -e
# ==============================================================================
# Core 06: OpenRC Supervise-Daemon Hardening & Proactive Healthcheck Watchdog
# Pure POSIX /bin/sh for Alpine Linux 3.24
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
. "$ROOT_DIR/lib/common.sh"
[ -f "$ROOT_DIR/config.env" ] && . "$ROOT_DIR/config.env"
. "$ROOT_DIR/lib/detect.sh"

log_info "Configuring OpenRC supervise-daemon auto-restart policies and Healthcheck Watchdog..."

apk_add smartmontools >/dev/null 2>&1 || true

# 1. Inject supervise-daemon respawn policies into existing /etc/conf.d/* files
ensure_supervise_conf() {
    local svc="$1"
    local conf_file="/etc/conf.d/$svc"
    [ -f "$conf_file" ] || return 0
    sed -i '/^supervisor=/d; /^respawn_delay=/d; /^respawn_max=/d; /^respawn_period=/d' "$conf_file"
    cat << 'EOF' >> "$conf_file"
supervisor="supervise-daemon"
respawn_delay=3
respawn_max=10
respawn_period=60
EOF
}

for svc in caddy vaultwarden forgejo conduit tuwunel navidrome transmission-daemon wsdd adguardhome tailscale; do
    ensure_supervise_conf "$svc"
done

# Fix respawn_period in custom init scripts if already deployed
for init_f in /etc/init.d/lxc-manager /etc/init.d/ttyd /etc/init.d/ytdlp-web /etc/init.d/beszel /etc/init.d/pocket-id /etc/init.d/sing-box; do
    if [ -f "$init_f" ] && ! grep -q "^respawn_period=" "$init_f"; then
        sed -i '/^respawn_max=/a respawn_period=60' "$init_f"
    fi
done

# 2. Deploy /usr/local/bin/homelab-watchdog.sh
cat << 'EOF' > /usr/local/bin/homelab-watchdog.sh
#!/bin/sh
set -e
# ==============================================================================
# Alpine Homelab Proactive Watchdog, L7 Brute-Force Guard & Alert Dispatcher
# Checks: RAM/OOM, Disk/Inodes, SMART Health, OpenRC Daemons, Caddy 401/403
# Channels: Telegram Bot API, Generic Webhook (Discord/Slack/ntfy/Gotify), Beszel Log
# ==============================================================================

CONFIG_FILE="/opt/homelab/config.env"
[ ! -f "$CONFIG_FILE" ] && [ -f "/etc/homelab/config.env" ] && CONFIG_FILE="/etc/homelab/config.env"
# shellcheck disable=SC1090
[ -f "$CONFIG_FILE" ] && . "$CONFIG_FILE"

WATCHDOG_RAM_MAX_PCT="${WATCHDOG_RAM_MAX_PCT:-90}"
WATCHDOG_DISK_MAX_PCT="${WATCHDOG_DISK_MAX_PCT:-85}"
WATCHDOG_COOLDOWN_SEC="${WATCHDOG_COOLDOWN_SEC:-3600}"
STATE_DIR="/var/run/homelab-watchdog"
HEALTH_JSON="/var/run/homelab-health.json"
LOG_FILE="/var/log/homelab-watchdog.log"
HOSTNAME_STR="$(hostname 2>/dev/null || echo homelab)"

mkdir -p "$STATE_DIR"

log_wd() {
    printf "%s [WATCHDOG] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

# Send alert with per-key cooldown deduplication
send_alert() {
    local key="$1"
    local severity="$2"
    local message="$3"
    local now
    now="$(date +%s)"
    local state_file="$STATE_DIR/${key}.ts"

    if [ -f "$state_file" ] && [ "$severity" != "RESOLVED" ]; then
        local last_ts
        last_ts="$(cat "$state_file" 2>/dev/null || echo 0)"
        if [ $((now - last_ts)) -lt "$WATCHDOG_COOLDOWN_SEC" ]; then
            return 0
        fi
    fi

    if [ "$severity" = "RESOLVED" ]; then
        rm -f "$state_file"
    else
        printf "%s\n" "$now" > "$state_file"
    fi

    log_wd "[$severity] ($key) $message"

    # Also append to Beszel log so Beszel Hub / logs viewer shows critical hardware/service events
    if [ -f /var/log/beszel.log ]; then
        printf "%s [HOMELAB-%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$severity" "$message" >> /var/log/beszel.log 2>/dev/null || true
    fi

    local icon="⚠️"
    [ "$severity" = "CRITICAL" ] && icon="🚨"
    [ "$severity" = "RESOLVED" ] && icon="✅"

    local full_text="${icon} [Homelab: ${HOSTNAME_STR}] ${severity}: ${message}"

    # 1. Telegram Bot API notification
    if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
        curl -sS -m 10 -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TG_CHAT_ID}" \
            --data-urlencode "text=${full_text}" >/dev/null 2>&1 || log_wd "WARN: Failed to send Telegram alert."
    fi

    # 2. Generic Webhook (Discord / Slack / ntfy / Gotify / Beszel / Custom)
    if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
        local escaped_msg
        escaped_msg=$(printf "%s" "$full_text" | sed 's/\\/\\\\/g; s/"/\\"/g')
        curl -sS -m 10 -X POST "$ALERT_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"content\": \"${escaped_msg}\", \"text\": \"${escaped_msg}\", \"severity\": \"${severity}\", \"host\": \"${HOSTNAME_STR}\"}" >/dev/null 2>&1 || log_wd "WARN: Failed to send Webhook alert."
    fi
}

mark_healthy() {
    local key="$1"
    local recovery_msg="$2"
    local state_file="$STATE_DIR/${key}.ts"
    if [ -f "$state_file" ]; then
        send_alert "$key" "RESOLVED" "$recovery_msg"
    fi
}

ISSUES_COUNT=0

# ------------------------------------------------------------------------------
# Check 1: RAM Usage & Kernel OOM Events
# ------------------------------------------------------------------------------
MEM_TOTAL=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
MEM_AVAIL=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
if [ -n "$MEM_TOTAL" ] && [ "$MEM_TOTAL" -gt 0 ]; then
    MEM_USED=$((MEM_TOTAL - MEM_AVAIL))
    MEM_PCT=$((MEM_USED * 100 / MEM_TOTAL))
    MEM_USED_MB=$((MEM_USED / 1024))
    MEM_TOTAL_MB=$((MEM_TOTAL / 1024))

    if [ "$MEM_PCT" -ge "$WATCHDOG_RAM_MAX_PCT" ]; then
        ISSUES_COUNT=$((ISSUES_COUNT + 1))
        # Proactive pagecache reclaim to prevent OOM kill
        sync
        echo 1 > /proc/sys/vm/drop_caches 2>/dev/null || true
        send_alert "ram_high" "CRITICAL" "Критическое потребление RAM: ${MEM_PCT}% (${MEM_USED_MB} / ${MEM_TOTAL_MB} МБ). Выполнен сброс pagecache."
    else
        mark_healthy "ram_high" "Потребление RAM вернулось в норму: ${MEM_PCT}% (${MEM_USED_MB} / ${MEM_TOTAL_MB} МБ)."
    fi
fi

# Check recent kernel OOM killer invocations
OOM_COUNT=$(dmesg 2>/dev/null | grep -c "Out of memory: Killed process" || true)
PREV_OOM_FILE="$STATE_DIR/oom_count.val"
PREV_OOM=0
[ -f "$PREV_OOM_FILE" ] && PREV_OOM=$(cat "$PREV_OOM_FILE" 2>/dev/null || echo 0)
if [ "$OOM_COUNT" -gt "$PREV_OOM" ]; then
    LAST_OOM=$(dmesg 2>/dev/null | grep "Out of memory: Killed process" | tail -n 1)
    printf "%s\n" "$OOM_COUNT" > "$PREV_OOM_FILE"
    send_alert "kernel_oom" "CRITICAL" "Ядро Linux вызвало OOM Killer! Детали: ${LAST_OOM}"
fi

# ------------------------------------------------------------------------------
# Check 2: Disk Space & Inode Exhaustion
# ------------------------------------------------------------------------------
CHECK_PATHS="/ ${STORAGE_BASE:-/srv/storage} ${BACKUP_DIR:-/var/backups/homelab}"
SEEN_DEVS=""
for mpath in $CHECK_PATHS; do
    [ ! -d "$mpath" ] && continue
    dev_info=$(df -P "$mpath" 2>/dev/null | awk 'NR==2 {print $1, $5, $4}')
    [ -z "$dev_info" ] && continue
    dev_name=$(printf "%s" "$dev_info" | awk '{print $1}')
    case " $SEEN_DEVS " in
        *" $dev_name "*) continue ;;
    esac
    SEEN_DEVS="$SEEN_DEVS $dev_name"

    use_pct=$(printf "%s" "$dev_info" | awk '{gsub(/%/,"",$2); print $2}')
    free_kb=$(printf "%s" "$dev_info" | awk '{print $3}')
    free_gb=$((free_kb / 1024 / 1024))
    safe_key="disk_$(printf "%s" "$mpath" | tr -c 'a-zA-Z0-9' '_')"

    if [ -n "$use_pct" ] && [ "$use_pct" -ge "$WATCHDOG_DISK_MAX_PCT" ]; then
        ISSUES_COUNT=$((ISSUES_COUNT + 1))
        send_alert "$safe_key" "CRITICAL" "Диск $mpath ($dev_name) заполнен на ${use_pct}% (свободно ~${free_gb} ГБ)!"
    else
        mark_healthy "$safe_key" "Свободное место на $mpath ($dev_name) в норме (${use_pct}% занято)."
    fi

    inode_pct=$(df -i "$mpath" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
    if [ -n "$inode_pct" ] && [ "$inode_pct" -ge 90 ] 2>/dev/null; then
        ISSUES_COUNT=$((ISSUES_COUNT + 1))
        send_alert "${safe_key}_inodes" "CRITICAL" "Исчерпание inodes на разделе $mpath: занято ${inode_pct}%!"
    fi
done

# ------------------------------------------------------------------------------
# Check 3: SMART Physical Disk Health
# ------------------------------------------------------------------------------
if command -v smartctl >/dev/null 2>&1; then
    for bdev in /dev/sd[a-z] /dev/nvme[0-9]n1; do
        [ ! -b "$bdev" ] && continue
        bname=$(basename "$bdev")
        smart_out=$(smartctl -H "$bdev" 2>/dev/null || true)
        # Skip devices without SMART capability (virtual machines, loop, etc.)
        printf "%s" "$smart_out" | grep -qiE "lacks SMART capability|Unavailable" && continue
        if printf "%s" "$smart_out" | grep -qiE "(test result: (FAILED|BAD)|health status:.*(FAILED|CRITICAL))"; then
            ISSUES_COUNT=$((ISSUES_COUNT + 1))
            send_alert "smart_${bname}" "CRITICAL" "СБОЙ SMART на физическом накопителе ${bdev}! Требуется срочная замена диска и проверка бэкапов Restic."
        fi
    done
fi

# ------------------------------------------------------------------------------
# Check 4: OpenRC Services Health & Automatic Self-Healing
# ------------------------------------------------------------------------------
check_and_heal_svc() {
    local flag="$1"
    local svc="$2"
    [ "$flag" != "yes" ] && return 0
    [ ! -x "/etc/init.d/$svc" ] && return 0

    if ! rc-service "$svc" status >/dev/null 2>&1; then
        ISSUES_COUNT=$((ISSUES_COUNT + 1))
        log_wd "WARN: Service '$svc' is down. Attempting automatic recovery..."
        if rc-service "$svc" restart >/dev/null 2>&1; then
            sleep 2
        fi
        if rc-service "$svc" status >/dev/null 2>&1; then
            send_alert "svc_${svc}" "WARNING" "Сервис OpenRC '${svc}' неожиданно остановился и был автоматически перезапущен Watchdog."
        else
            send_alert "svc_${svc}" "CRITICAL" "Сервис OpenRC '${svc}' УПАЛ и НЕ СМОГ перезапуститься автоматически! Проверьте: homelab logs ${svc}"
        fi
    else
        mark_healthy "svc_${svc}" "Сервис OpenRC '${svc}' снова работает стабильно."
    fi
}

check_and_heal_svc "${ENABLE_CADDY:-yes}" "caddy"
check_and_heal_svc "${ENABLE_SINGBOX:-yes}" "sing-box"
check_and_heal_svc "${ENABLE_VAULTWARDEN:-no}" "vaultwarden"
check_and_heal_svc "${ENABLE_FORGEJO:-no}" "forgejo"
check_and_heal_svc "${ENABLE_CONDUIT:-no}" "conduit"
check_and_heal_svc "${ENABLE_NAVIDROME:-no}" "navidrome"
check_and_heal_svc "${ENABLE_TRANSMISSION:-no}" "transmission-daemon"
check_and_heal_svc "${ENABLE_SAMBA:-no}" "samba"
check_and_heal_svc "${ENABLE_SAMBA:-no}" "wsdd"
check_and_heal_svc "${ENABLE_YTDLP:-no}" "ytdlp-web"
check_and_heal_svc "${ENABLE_LXC_MANAGER:-no}" "lxc-manager"
check_and_heal_svc "${ENABLE_LXC_MANAGER:-no}" "ttyd"
check_and_heal_svc "${ENABLE_BESZEL:-yes}" "beszel"
check_and_heal_svc "${ENABLE_POCKET_ID:-no}" "pocket-id"
check_and_heal_svc "${ENABLE_ADGUARD_HOME:-no}" "adguardhome"
check_and_heal_svc "${ENABLE_TAILSCALE:-no}" "tailscale"

# ------------------------------------------------------------------------------
# Check 5: Caddy HTTP 401/403 Brute-Force Feeder into nftables @web_banlist
# ------------------------------------------------------------------------------
CADDY_ACCESS_LOG="/var/log/caddy/access.log"
if [ -f "$CADDY_ACCESS_LOG" ] && command -v nft >/dev/null 2>&1; then
    OFFENDING_IPS=$(tail -n 400 "$CADDY_ACCESS_LOG" 2>/dev/null | python3 -c '
import sys, json, collections
counts = collections.Counter()
for line in sys.stdin:
    try:
        rec = json.loads(line)
        status = int(rec.get("status", 0))
        if status in (401, 403):
            ip = rec.get("request", {}).get("remote_ip", "")
            if ip and not ip.startswith(("127.", "::1")):
                counts[ip] += 1
    except Exception:
        pass
for ip, cnt in counts.items():
    if cnt >= 8:
        print(ip)
' 2>/dev/null || true)

    for bad_ip in $OFFENDING_IPS; do
        if nft add element inet homelab web_banlist "{ $bad_ip timeout 15m }" 2>/dev/null; then
            send_alert "bruteforce_${bad_ip}" "WARNING" "Заблокирован IP ${bad_ip} в nftables @web_banlist (15m) за перебор паролей HTTP 401/403 в Caddy."
        fi
    done
fi

# ------------------------------------------------------------------------------
# Write summary status for Dashboard / Beszel integration
# ------------------------------------------------------------------------------
cat << EOJ > "$HEALTH_JSON"
{
  "timestamp": $(date +%s),
  "status": "$( [ "$ISSUES_COUNT" -eq 0 ] && echo "healthy" || echo "degraded" )",
  "issues_count": $ISSUES_COUNT,
  "ram_percent": ${MEM_PCT:-0}
}
EOJ
EOF

chmod +x /usr/local/bin/homelab-watchdog.sh

# 3. Register Watchdog in root crontab (runs every 2 minutes)
mkdir -p /etc/crontabs
touch /etc/crontabs/root
sed -i '/homelab-watchdog\.sh/d' /etc/crontabs/root
echo "*/2 * * * * /usr/local/bin/homelab-watchdog.sh >/dev/null 2>&1" >> /etc/crontabs/root

rc-update add crond default >/dev/null 2>&1 || true
rc-service crond restart >/dev/null 2>&1 || true

log_success "OpenRC supervise-daemon policies applied and homelab-watchdog active (every 2m via crond)."
