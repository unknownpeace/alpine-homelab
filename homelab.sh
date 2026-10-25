#!/bin/sh
set -e

# ==============================================================================
# Alpine Homelab Appliance - CLI Orchestrator
# ==============================================================================

SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
LIB_DIR="$SCRIPT_DIR/lib"
CORE_DIR="$SCRIPT_DIR/core"
SERVICES_DIR="$SCRIPT_DIR/services"

# 1. Source shared libraries
# shellcheck source=lib/common.sh
. "$LIB_DIR/common.sh"

# 2. Load custom config if present
if [ -f "/opt/homelab/config.env" ]; then
    # shellcheck disable=SC1091
    . "/opt/homelab/config.env"
elif [ -f "$SCRIPT_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/config.env"
fi

# 3. Detect environment
# shellcheck source=lib/detect.sh
. "$LIB_DIR/detect.sh"

usage() {
    echo -e "${C_CYAN}${C_BOLD}Alpine Homelab CLI Orchestrator${C_RESET}"
    echo ""
    echo "Usage: homelab <command> [arguments]"
    echo ""
    echo "Commands:"
    echo "  install            Run complete modular deployment (core + enabled services)"
    echo "  reconfigure        Re-run setup wizard to change services, passwords, or proxy"
    echo "  update             Pull git updates, refresh rules, and restart services"
    echo "  doctor             Run full automated system health & diagnostics audit"
    echo "  status             Show quick summary of running services and resources"
    echo "  backup [dir]       Create atomic disaster recovery backup of DBs & configs"
    echo "  restore <file>     Restore full homelab state from a backup archive"
    echo "  ip                 Display host network addresses and interface info"
    echo "  cert               Show root CA certificate details and install instructions"
    echo "  mode [gw|srv]      Switch between Home Gateway and Standalone mode"
    echo "  proxy [node|url]   Update sing-box outbound proxy/subscription or refresh"
    echo "  tailscale <act>    Manage Tailscale Zero-Trust mesh VPN (up, down, status)"
    echo "  service <act> <svc> Manage a service (start, stop, restart, status, logs)"
    echo "  help               Display this help message"
    echo ""
    exit 0
}

cmd_install() {
    check_root
    echo -e "${C_BLUE}================================================================${C_RESET}"
    echo -e "${C_BLUE}    Zero-Docker Alpine Homelab Appliance (Modular Edition)      ${C_RESET}"
    echo -e "${C_BLUE}================================================================${C_RESET}"
    if [ -f "/opt/homelab/config.env" ]; then
        log_info "Using saved configuration: /opt/homelab/config.env"
        log_info "To re-run the setup wizard with new choices, run: homelab reconfigure"
    fi
    log_info "Host Primary IP : $IP_HOST (Interface: $PHYSICAL_IF)"
    log_info "Base Domain     : *.$BASE_DOMAIN"
    log_info "Auth Username   : $AUTH_USER"
    log_info "System Timezone : $TIMEZONE"
    log_info "Storage Base    : $STORAGE_BASE"
    log_info "Gateway Mode    : $GATEWAY_MODE"
    log_info "Outbound Proxy  : $(echo "$SUB_URL" | cut -c1-60)..."
    echo ""

    # Execute Core Infrastructure
    log_info ">>> Phase 1: Core Host Infrastructure <<<"
    sh "$CORE_DIR/01-packages.sh"
    sh "$CORE_DIR/02-sysctl.sh"
    sh "$CORE_DIR/03-network.sh"
    sh "$CORE_DIR/04-storage.sh"
    sh "$CORE_DIR/05-firewall.sh"

    # Execute Services based on ENABLE_* flags
    log_info ">>> Phase 2: Homelab Services Deployment <<<"
    [ "${ENABLE_CADDY:-yes}" = "yes" ] && sh "$SERVICES_DIR/caddy/install.sh"
    [ "${ENABLE_SINGBOX:-yes}" = "yes" ] && sh "$SERVICES_DIR/sing-box/install.sh"
    [ "${ENABLE_DASHBOARD:-yes}" = "yes" ] && sh "$SERVICES_DIR/dashboard/install.sh"

    sync_service_state() {
        local flag="$1"
        local svc_script="$2"
        local openrc_name="$3"
        local snippet_name="${4:-$openrc_name}"
        if [ "$flag" = "yes" ]; then
            sh "$svc_script"
        else
            if rc-service "$openrc_name" status >/dev/null 2>&1; then
                log_info "Stopping disabled service: $openrc_name..."
                rc-service "$openrc_name" stop >/dev/null 2>&1 || true
                rc-update del "$openrc_name" default >/dev/null 2>&1 || true
            fi
            if [ "$openrc_name" = "lxc-manager" ] && rc-service ttyd status >/dev/null 2>&1; then
                rc-service ttyd stop >/dev/null 2>&1 || true
                rc-update del ttyd default >/dev/null 2>&1 || true
            fi
            if [ "$openrc_name" = "samba" ] && rc-service wsdd status >/dev/null 2>&1; then
                rc-service wsdd stop >/dev/null 2>&1 || true
                rc-update del wsdd default >/dev/null 2>&1 || true
            fi
            [ -n "$snippet_name" ] && remove_caddy_snippet "$snippet_name"
        fi
    }

    sync_service_state "${ENABLE_VAULTWARDEN:-no}" "$SERVICES_DIR/vaultwarden/install.sh" "vaultwarden"
    sync_service_state "${ENABLE_FORGEJO:-no}" "$SERVICES_DIR/forgejo/install.sh" "forgejo"
    sync_service_state "${ENABLE_CONDUIT:-no}" "$SERVICES_DIR/conduit/install.sh" "conduit"
    sync_service_state "${ENABLE_NAVIDROME:-no}" "$SERVICES_DIR/navidrome/install.sh" "navidrome"
    sync_service_state "${ENABLE_TRANSMISSION:-no}" "$SERVICES_DIR/transmission/install.sh" "transmission-daemon" "transmission"
    sync_service_state "${ENABLE_SAMBA:-no}" "$SERVICES_DIR/samba/install.sh" "samba" ""
    sync_service_state "${ENABLE_YTDLP:-no}" "$SERVICES_DIR/ytdlp-web/install.sh" "ytdlp-web" "ytdlp"
    sync_service_state "${ENABLE_LXC_MANAGER:-no}" "$SERVICES_DIR/lxc-manager/install.sh" "lxc-manager"
    sync_service_state "${ENABLE_ADGUARD_HOME:-no}" "$SERVICES_DIR/adguardhome/install.sh" "adguardhome"
    sync_service_state "${ENABLE_TAILSCALE:-no}" "$SERVICES_DIR/tailscale/install.sh" "tailscale" ""

    # Finalize host DNS resolver to local engine after all package downloads complete
    if [ "${ENABLE_SINGBOX:-yes}" = "yes" ] || [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ]; then
        log_info "Switching host resolver (/etc/resolv.conf) to local DNS engine..."
        cat << EOF > /etc/resolv.conf
nameserver 127.0.0.1
nameserver 1.1.1.1
nameserver 77.88.8.8
EOF
    fi

    if [ "${ENABLE_CADDY:-yes}" = "yes" ] && rc-service caddy status >/dev/null 2>&1; then
        rc-service caddy reload >/dev/null 2>&1 || rc-service caddy restart >/dev/null 2>&1 || true
    fi

    # Print Summary Cheat Sheet
    cmd_summary
}

cmd_reconfigure() {
    check_root
    echo -e "${C_CYAN}================================================================${C_RESET}"
    echo -e "${C_CYAN}    Alpine Homelab Appliance - Reconfiguration Wizard           ${C_RESET}"
    echo -e "${C_CYAN}================================================================${C_RESET}"
    local cfg="/opt/homelab/config.env"
    [ ! -f "$cfg" ] && cfg="$SCRIPT_DIR/config.env"
    if [ -f "$cfg" ]; then
        cp -f "$cfg" "${cfg}.bak"
        log_info "Current configuration backed up to ${cfg}.bak"
    fi
    export RECONFIG_MODE=1
    . "$LIB_DIR/detect.sh"
    cmd_install
}

cmd_summary() {
    echo ""
    echo -e "${C_GREEN}${C_BOLD}================================================================================${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}      HOMELAB BARE-METAL APPLIANCE DEPLOYED SUCCESSFULLY!                       ${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}================================================================================${C_RESET}"
    echo ""
    if [ "${GATEWAY_MODE:-yes}" = "yes" ]; then
        echo -e "${C_PURPLE}${C_BOLD}1. ЧТО ПРОПИСАТЬ НА УСТРОЙСТВАХ (Смартфон, ПК, Ноутбук, ТВ):${C_RESET}"
        echo -e "  ------------------------------------------------------------------------------"
        echo -e "  ${C_WHITE}Основной шлюз (Default Gateway):${C_RESET} ${C_YELLOW}${C_BOLD}$IP_HOST${C_RESET}   (Прозрачный VLESS-шлюз через sing-box)"
        echo -e "  ${C_WHITE}Основной DNS (DNS Server 1)    :${C_RESET} ${C_YELLOW}${C_BOLD}$IP_HOST${C_RESET}   (sing-box: фильтрация рекламы + *.$BASE_DOMAIN)"
        echo -e "  ${C_WHITE}Резервный DNS (DNS Server 2)   :${C_RESET} ${C_YELLOW}${C_BOLD}1.1.1.1${C_RESET}   (или адрес домашнего роутера)"
        echo ""
    fi
    echo -e "${C_PURPLE}${C_BOLD}2. СЕРТИФИКАТ ДЛЯ ЗЕЛЕНОГО HTTPS (БЕЗ ПРЕДУПРЕЖДЕНИЙ БРАУЗЕРА):${C_RESET}"
    echo -e "  ------------------------------------------------------------------------------"
    echo -e "  Файл сертификата : ${C_GREEN}/root/caddy-homelab-root.crt${C_RESET}  (или прямо с дашборда)"
    echo -e "  Скачайте его и установите в «Доверенные корневые центры сертификации» на ПК/Телефоне."
    echo ""
    echo -e "${C_PURPLE}${C_BOLD}3. ДОСТУП К ВЕБ-СЕРВИСАМ (Локальный защищенный HTTPS):${C_RESET}"
    echo -e "  ------------------------------------------------------------------------------"
    echo -e "  ${C_WHITE}Сервис${C_RESET}          | ${C_WHITE}Адрес домена${C_RESET}               | ${C_WHITE}Локальный порт${C_RESET}   | ${C_WHITE}Авторизация${C_RESET}"
    echo -e "  ------------------------------------------------------------------------------"
    [ "${ENABLE_DASHBOARD:-yes}" = "yes" ]   && echo -e "  Портал / Дашборд: ${C_GREEN}${C_BOLD}https://homelab.$BASE_DOMAIN${C_RESET}      https://$IP_HOST        — (Главная страница)"
    [ "${ENABLE_VAULTWARDEN:-yes}" = "yes" ] && echo -e "  Vaultwarden     : ${C_CYAN}https://vault.$BASE_DOMAIN${C_RESET}        127.0.0.1:8000           Встроенная (Web Vault)"
    [ "${ENABLE_FORGEJO:-yes}" = "yes" ]     && echo -e "  Forgejo Git     : ${C_CYAN}https://git.$BASE_DOMAIN${C_RESET}          127.0.0.1:3000           Встроенная (SSH: 2222)"
    [ "${ENABLE_CONDUIT:-yes}" = "yes" ]     && echo -e "  Matrix Conduit  : ${C_CYAN}https://matrix.$BASE_DOMAIN${C_RESET}       127.0.0.1:6167           Токены клиентов Matrix"
    [ "${ENABLE_NAVIDROME:-yes}" = "yes" ]   && echo -e "  Navidrome Music : ${C_CYAN}https://music.$BASE_DOMAIN${C_RESET}        127.0.0.1:4533           Встроенная (Subsonic)"
    [ "${ENABLE_TRANSMISSION:-yes}" = "yes" ] && echo -e "  Transmission    : ${C_CYAN}https://torrent.$BASE_DOMAIN${C_RESET}      127.0.0.1:9091           ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    [ "${ENABLE_YTDLP:-yes}" = "yes" ]       && echo -e "  Media Downloader: ${C_CYAN}https://ytdl.$BASE_DOMAIN${C_RESET}         127.0.0.1:8080           ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    local sb_dns_info="53"
    [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ] && sb_dns_info="5353 (upstream)"
    [ "${ENABLE_SINGBOX:-yes}" = "yes" ]     && echo -e "  sing-box UI/DNS : ${C_CYAN}https://proxy.$BASE_DOMAIN/ui/${C_RESET}    127.0.0.1:9090 (DNS: $sb_dns_info) ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ] && echo -e "  LXC Sandbox Mgr : ${C_CYAN}https://lxc.$BASE_DOMAIN${C_RESET}          127.0.0.1:8888, 7681     ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}  (LAN DHCP, Snapshots, ttyd)"
    [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ] && echo -e "  AdGuard Home    : ${C_CYAN}https://adguard.$BASE_DOMAIN${C_RESET}      127.0.0.1:3001 (DNS: 53) ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    echo ""
    if [ "${ENABLE_SAMBA:-yes}" = "yes" ]; then
        echo -e "${C_PURPLE}${C_BOLD}4. СЕТЕВЫЕ ПАПКИ SAMBA (Проводник Windows):${C_RESET}"
        echo -e "  ------------------------------------------------------------------------------"
        echo -e "  ${C_WHITE}Общее хранилище  :${C_RESET} \\\\$IP_HOST\\Storage   ${C_CYAN}или${C_RESET}  \\\\HOMELAB\\Storage"
        echo -e "  ${C_WHITE}Папка с Музыкой  :${C_RESET} \\\\$IP_HOST\\Music     ${C_CYAN}или${C_RESET}  \\\\HOMELAB\\Music      (-> $STORAGE_BASE/music)"
        echo -e "  ${C_WHITE}Папка с Загрузками:${C_RESET} \\\\$IP_HOST\\Downloads ${C_CYAN}или${C_RESET}  \\\\HOMELAB\\Downloads  (-> $STORAGE_BASE/downloads)"
        echo -e "  ${C_WHITE}Пользователь SMB :${C_RESET} ${C_YELLOW}$AUTH_USER${C_RESET} | ${C_WHITE}Пароль SMB:${C_RESET} ${C_YELLOW}$AUTH_PASS${C_RESET}"
        echo ""
    fi
    echo -e "${C_PURPLE}${C_BOLD}5. РЕЗЕРВНОЕ КОПИРОВАНИЕ И УДАЛЕННЫЙ ДОСТУП:${C_RESET}"
    echo -e "  ------------------------------------------------------------------------------"
    echo -e "  ${C_WHITE}Создать бэкап баз данных:${C_RESET} ${C_CYAN}homelab backup${C_RESET}           (в $STORAGE_BASE/backups)"
    echo -e "  ${C_WHITE}Восстановить из бэкапа  :${C_RESET} ${C_CYAN}homelab restore <файл.tar.gz>${C_RESET}"
    echo -e "  ${C_WHITE}Удаленный доступ VPN    :${C_RESET} ${C_CYAN}homelab tailscale up${C_RESET}     (без белого IP)"
    echo ""
    echo -e "${C_GREEN}Команда управления комплексом: ${C_CYAN}homelab doctor${C_RESET} / ${C_CYAN}homelab update${C_RESET}"
}

cmd_update() {
    check_root
    log_info "Updating Alpine Homelab..."
    if [ -d "$SCRIPT_DIR/.git" ]; then
        log_info "Pulling latest git changes..."
        git -C "$SCRIPT_DIR" pull --ff-only || log_warn "Git pull failed or local changes present."
    fi

    if [ "${ENABLE_SINGBOX:-yes}" = "yes" ]; then
        log_info "Updating sing-box anti-censorship and DNS rule-sets..."
        sh "$SERVICES_DIR/sing-box/update-rules.sh"
        rc-service sing-box restart || true
    fi

    log_info "Reloading Caddy reverse proxy..."
    rc-service caddy reload 2>/dev/null || rc-service caddy restart || true

    if [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ]; then
        rc-service lxc-manager restart || true
        rc-service ttyd restart || true
    fi

    log_success "Homelab updated successfully."
}

cmd_doctor() {
    echo -e "${C_CYAN}${C_BOLD}=== Alpine Homelab Diagnostics (Doctor) ===${C_RESET}"
    local issues=0

    check_svc() {
        local s="$1"
        if rc-service "$s" status >/dev/null 2>&1; then
            echo -e "  [${C_GREEN}PASS${C_RESET}] OpenRC Service: $s is running"
        else
            echo -e "  [${C_RED}FAIL${C_RESET}] OpenRC Service: $s is STOPPED or missing"
            issues=$((issues + 1))
        fi
    }

    echo ""
    echo -e "${C_WHITE}Checking OpenRC Services:${C_RESET}"
    [ "${ENABLE_SINGBOX:-yes}" = "yes" ] && check_svc "sing-box"
    [ "${ENABLE_CADDY:-yes}" = "yes" ] && check_svc "caddy"
    [ "${ENABLE_VAULTWARDEN:-yes}" = "yes" ] && check_svc "vaultwarden"
    [ "${ENABLE_FORGEJO:-yes}" = "yes" ] && check_svc "forgejo"
    [ "${ENABLE_CONDUIT:-yes}" = "yes" ] && check_svc "conduit"
    [ "${ENABLE_NAVIDROME:-yes}" = "yes" ] && check_svc "navidrome"
    [ "${ENABLE_TRANSMISSION:-yes}" = "yes" ] && check_svc "transmission-daemon"
    [ "${ENABLE_SAMBA:-yes}" = "yes" ] && { check_svc "samba"; check_svc "wsdd"; }
    [ "${ENABLE_YTDLP:-yes}" = "yes" ] && check_svc "ytdlp-web"
    [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ] && { check_svc "lxc-manager"; check_svc "ttyd"; check_svc "lxc"; }
    [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ] && check_svc "adguardhome"
    [ "${ENABLE_TAILSCALE:-no}" = "yes" ] && check_svc "tailscale"

    echo ""
    echo -e "${C_WHITE}Checking Network Bridges:${C_RESET}"
    local bridges=""
    [ "${BRIDGE_ENABLE:-yes}" = "yes" ] && bridges="br0"
    [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ] && bridges="$bridges lxcbr0"
    for br in $bridges; do
        if ip link show "$br" >/dev/null 2>&1; then
            echo -e "  [${C_GREEN}PASS${C_RESET}] Bridge $br exists and is UP"
        else
            echo -e "  [${C_RED}FAIL${C_RESET}] Bridge $br missing"
            issues=$((issues + 1))
        fi
    done

    echo ""
    echo -e "${C_WHITE}Checking DNS Resolution (port 53):${C_RESET}"
    if nslookup "homelab.$BASE_DOMAIN" 127.0.0.1 >/dev/null 2>&1; then
        echo -e "  [${C_GREEN}PASS${C_RESET}] homelab.$BASE_DOMAIN resolves locally"
    else
        echo -e "  [${C_RED}FAIL${C_RESET}] homelab.$BASE_DOMAIN failed to resolve via 127.0.0.1"
        issues=$((issues + 1))
    fi

    if nslookup ya.ru 127.0.0.1 >/dev/null 2>&1; then
        echo -e "  [${C_GREEN}PASS${C_RESET}] External DNS (ya.ru) resolves"
    else
        echo -e "  [${C_RED}FAIL${C_RESET}] External DNS failed"
        issues=$((issues + 1))
    fi

    echo ""
    echo -e "${C_WHITE}Checking HTTPS Endpoints:${C_RESET}"
    local endpoints="homelab"
    [ "${ENABLE_VAULTWARDEN:-yes}" = "yes" ] && endpoints="$endpoints vault"
    [ "${ENABLE_FORGEJO:-yes}" = "yes" ]     && endpoints="$endpoints git"
    [ "${ENABLE_CONDUIT:-yes}" = "yes" ]     && endpoints="$endpoints matrix"
    [ "${ENABLE_NAVIDROME:-yes}" = "yes" ]   && endpoints="$endpoints music"
    [ "${ENABLE_TRANSMISSION:-yes}" = "yes" ] && endpoints="$endpoints torrent"
    [ "${ENABLE_YTDLP:-yes}" = "yes" ]       && endpoints="$endpoints ytdl"
    [ "${ENABLE_SINGBOX:-yes}" = "yes" ]     && endpoints="$endpoints proxy"
    [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ] && endpoints="$endpoints lxc"
    [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ] && endpoints="$endpoints adguard"
    for prefix in $endpoints; do
        local d="$prefix.$BASE_DOMAIN"
        local code
        code=$(curl -s -k -o /dev/null -w "%{http_code}" --resolve "$d:443:127.0.0.1" "https://$d/" || echo "000")
        if [ "$code" = "200" ] || [ "$code" = "302" ] || [ "$code" = "401" ]; then
            echo -e "  [${C_GREEN}PASS${C_RESET}] https://$d/ returned HTTP $code"
        else
            echo -e "  [${C_RED}FAIL${C_RESET}] https://$d/ returned HTTP $code"
            issues=$((issues + 1))
        fi
    done

    echo ""
    echo -e "${C_WHITE}System Resources:${C_RESET}"
    free -h | awk 'NR==2{printf "  RAM: Used %s / Total %s (Available: %s)\n", $3, $2, $7}'
    df -h / | awk 'NR==2{printf "  Disk (/): Used %s / Total %s (%s)\n", $3, $2, $5}'

    echo ""
    if [ "$issues" -eq 0 ]; then
        echo -e "${C_GREEN}${C_BOLD}Doctor Result: All systems operational! (0 issues found)${C_RESET}"
        return 0
    else
        echo -e "${C_RED}${C_BOLD}Doctor Result: Found $issues issue(s) that require attention.${C_RESET}"
        return 1
    fi
}

cmd_status() {
    echo -e "${C_CYAN}${C_BOLD}=== Homelab Status Summary ===${C_RESET}"
    rc-status default
    echo ""
    echo -e "${C_WHITE}Active Containers (LXC):${C_RESET}"
    lxc-ls -1 --active || echo "None"
    echo ""
    free -h
}

cmd_service() {
    check_root
    local act="${1:-}"
    local svc="${2:-}"
    if [ -z "$act" ] || [ -z "$svc" ]; then
        echo "Usage: homelab service <start|stop|restart|status|logs> <service_name>"
        exit 1
    fi
    case "$act" in
        logs)
            if [ -f "/var/log/$svc.log" ]; then
                tail -n 50 "/var/log/$svc.log"
            elif [ -f "/var/log/$svc/$svc.log" ]; then
                tail -n 50 "/var/log/$svc/$svc.log"
            else
                rc-service "$svc" status
            fi
            ;;
        *)
            rc-service "$svc" "$act"
            ;;
    esac
}

cmd_backup() {
    check_root
    local dest="${1:-$STORAGE_BASE/backups}"
    mkdir -p "$dest"
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local target="$dest/homelab_backup_$ts.tar.gz"
    local tmp
    tmp="$(mktemp -d)"

    log_info "Creating atomic disaster recovery backup to $target..."

    # 1. Hot SQLite databases
    if [ -f /var/lib/vaultwarden/db.sqlite3 ]; then
        mkdir -p "$tmp/vaultwarden"
        sqlite3 /var/lib/vaultwarden/db.sqlite3 ".backup '$tmp/vaultwarden/db.sqlite3'" 2>/dev/null || cp -f /var/lib/vaultwarden/db.sqlite3 "$tmp/vaultwarden/"
        cp -f /var/lib/vaultwarden/rsa_key* "$tmp/vaultwarden/" 2>/dev/null || true
        cp -f /var/lib/vaultwarden/.env "$tmp/vaultwarden/" 2>/dev/null || true
    fi

    if [ -f /var/lib/forgejo/data/forgejo.db ]; then
        mkdir -p "$tmp/forgejo"
        sqlite3 /var/lib/forgejo/data/forgejo.db ".backup '$tmp/forgejo/forgejo.db'" 2>/dev/null || cp -f /var/lib/forgejo/data/forgejo.db "$tmp/forgejo/"
        cp -rf /etc/forgejo "$tmp/forgejo/etc" 2>/dev/null || true
        cp -rf /var/lib/forgejo/data/forgejo-repositories "$tmp/forgejo/repos" 2>/dev/null || true
    fi

    if [ -f /var/lib/navidrome/navidrome.db ]; then
        mkdir -p "$tmp/navidrome"
        sqlite3 /var/lib/navidrome/navidrome.db ".backup '$tmp/navidrome/navidrome.db'" 2>/dev/null || cp -f /var/lib/navidrome/navidrome.db "$tmp/navidrome/"
    fi

    # 2. Conduit Matrix
    if [ -d /var/lib/conduit ]; then
        mkdir -p "$tmp/conduit"
        cp -rf /var/lib/conduit "$tmp/conduit/data" 2>/dev/null || true
        cp -rf /etc/conduit "$tmp/conduit/etc" 2>/dev/null || true
    fi

    # 3. Transmission config
    if [ -d /var/lib/transmission/config ]; then
        mkdir -p "$tmp/transmission"
        cp -rf /var/lib/transmission/config "$tmp/transmission/" 2>/dev/null || true
    fi

    # 4. AdGuard Home config
    if [ -d /etc/adguardhome ]; then
        mkdir -p "$tmp/adguardhome"
        cp -rf /etc/adguardhome "$tmp/adguardhome/" 2>/dev/null || true
    fi

    # 5. LXC Containers configuration
    if [ -d /var/lib/lxc ]; then
        mkdir -p "$tmp/lxc"
        for cdir in /var/lib/lxc/*; do
            if [ -f "$cdir/config" ]; then
                cname=$(basename "$cdir")
                mkdir -p "$tmp/lxc/$cname"
                cp -f "$cdir/config" "$tmp/lxc/$cname/" 2>/dev/null || true
            fi
        done
    fi

    # 6. System config, Firewall & Caddy PKI
    mkdir -p "$tmp/system"
    [ -f "/opt/homelab/config.env" ] && cp -f "/opt/homelab/config.env" "$tmp/system/config.env"
    [ -f "/etc/nftables.nft" ] && cp -f "/etc/nftables.nft" "$tmp/system/nftables.nft"
    [ -d "/var/lib/caddy/.local/share/caddy/pki" ] && cp -rf "/var/lib/caddy/.local/share/caddy/pki" "$tmp/system/pki"

    # 7. Compress
    tar -czf "$target" -C "$tmp" .
    rm -rf "$tmp"

    # 8. Rotation (keep 7 newest)
    find "$dest" -maxdepth 1 -name "homelab_backup_*.tar.gz" | sort -r | tail -n +8 | xargs rm -f 2>/dev/null || true

    log_success "Backup created: $target ($(du -h "$target" | awk '{print $1}'))"
}

cmd_restore() {
    check_root
    local src="${1:-}"
    if [ -z "$src" ] || [ ! -f "$src" ]; then
        log_error "Usage: homelab restore <path_to_backup.tar.gz>"
        exit 1
    fi

    log_warn "Starting full disaster recovery restore from $src..."
    printf "Are you sure you want to restore? This will overwrite current state [y/N]: "
    read -r ans
    if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
        log_info "Restore cancelled by user."
        exit 0
    fi

    local tmp
    tmp="$(mktemp -d)"
    tar -xzf "$src" -C "$tmp"

    log_info "Stopping services for atomic database restore..."
    rc-service vaultwarden stop >/dev/null 2>&1 || true
    rc-service forgejo stop >/dev/null 2>&1 || true
    rc-service conduit stop >/dev/null 2>&1 || true
    rc-service navidrome stop >/dev/null 2>&1 || true
    rc-service transmission-daemon stop >/dev/null 2>&1 || true
    rc-service adguardhome stop >/dev/null 2>&1 || true

    # Restore Vaultwarden
    if [ -d "$tmp/vaultwarden" ]; then
        mkdir -p /var/lib/vaultwarden
        cp -rf "$tmp/vaultwarden/"* /var/lib/vaultwarden/
        chown -R vaultwarden:vaultwarden /var/lib/vaultwarden
    fi

    # Restore Forgejo
    if [ -d "$tmp/forgejo" ]; then
        mkdir -p /var/lib/forgejo/data /etc/forgejo
        [ -f "$tmp/forgejo/forgejo.db" ] && cp -f "$tmp/forgejo/forgejo.db" /var/lib/forgejo/data/forgejo.db
        [ -d "$tmp/forgejo/etc" ] && cp -rf "$tmp/forgejo/etc/"* /etc/forgejo/
        [ -d "$tmp/forgejo/repos" ] && cp -rf "$tmp/forgejo/repos/"* /var/lib/forgejo/data/forgejo-repositories/
        chown -R forgejo:www-data /etc/forgejo /var/lib/forgejo
    fi

    # Restore Conduit
    if [ -d "$tmp/conduit" ]; then
        mkdir -p /var/lib/conduit /etc/conduit
        cp -rf "$tmp/conduit/data/"* /var/lib/conduit/
        [ -d "$tmp/conduit/etc" ] && cp -rf "$tmp/conduit/etc/"* /etc/conduit/
        chown -R conduit:conduit /var/lib/conduit /etc/conduit
    fi

    # Restore Navidrome
    if [ -d "$tmp/navidrome" ]; then
        mkdir -p /var/lib/navidrome
        cp -f "$tmp/navidrome/navidrome.db" /var/lib/navidrome/navidrome.db
        chown -R navidrome:media /var/lib/navidrome
    fi

    # Restore Transmission
    if [ -d "$tmp/transmission" ]; then
        mkdir -p /var/lib/transmission/config
        cp -rf "$tmp/transmission/config/"* /var/lib/transmission/config/
        chown -R transmission:media /var/lib/transmission
    fi

    # Restore AdGuard Home
    if [ -d "$tmp/adguardhome" ]; then
        mkdir -p /etc/adguardhome
        cp -rf "$tmp/adguardhome/"* /etc/adguardhome/
    fi

    # Restore LXC Configs
    if [ -d "$tmp/lxc" ]; then
        for cdir in "$tmp/lxc/"*; do
            if [ -d "$cdir" ]; then
                cname=$(basename "$cdir")
                mkdir -p "/var/lib/lxc/$cname"
                [ -f "$cdir/config" ] && cp -f "$cdir/config" "/var/lib/lxc/$cname/config"
            fi
        done
    fi

    # Restore System, Firewall & PKI
    if [ -d "$tmp/system" ]; then
        [ -f "$tmp/system/config.env" ] && cp -f "$tmp/system/config.env" /opt/homelab/config.env
        if [ -f "$tmp/system/nftables.nft" ]; then
            cp -f "$tmp/system/nftables.nft" /etc/nftables.nft
            rc-service nftables restart >/dev/null 2>&1 || true
        fi
        if [ -d "$tmp/system/pki" ]; then
            mkdir -p /var/lib/caddy/.local/share/caddy
            cp -rf "$tmp/system/pki" /var/lib/caddy/.local/share/caddy/
            chown -R caddy:caddy /var/lib/caddy
        fi
    fi

    rm -rf "$tmp"

    log_info "Restarting restored services..."
    rc-service vaultwarden start >/dev/null 2>&1 || true
    rc-service forgejo start >/dev/null 2>&1 || true
    rc-service conduit start >/dev/null 2>&1 || true
    rc-service navidrome start >/dev/null 2>&1 || true
    rc-service transmission-daemon start >/dev/null 2>&1 || true
    rc-service adguardhome start >/dev/null 2>&1 || true
    rc-service caddy reload >/dev/null 2>&1 || rc-service caddy restart >/dev/null 2>&1 || true

    log_success "Disaster recovery restore completed successfully."
    cmd_doctor
}

cmd_tailscale() {
    check_root
    local sub="${1:-status}"
    shift || true
    case "$sub" in
        up)
            if ! command -v tailscale >/dev/null 2>&1; then
                sh "$SERVICES_DIR/tailscale/install.sh"
            fi
            rc-service tailscale start >/dev/null 2>&1 || true
            tailscale up "$@"
            ;;
        down)
            tailscale down
            ;;
        status)
            tailscale status
            ;;
        install)
            sh "$SERVICES_DIR/tailscale/install.sh"
            ;;
        *)
            echo "Usage: homelab tailscale <up|down|status|install> [args]"
            ;;
    esac
}

cmd_ip() {
    echo -e "${C_CYAN}${C_BOLD}Host Network Configuration:${C_RESET}"
    echo "  Primary IP     : $IP_HOST"
    echo "  Default Gateway: $DEFAULT_GW"
    echo "  Interface      : $PHYSICAL_IF"
    echo "  Base Domain    : *.$BASE_DOMAIN"
    echo "  DNS Resolver   : $IP_HOST:53"
}

cmd_cert() {
    local cert_file="/root/caddy-homelab-root.crt"
    if [ ! -f "$cert_file" ]; then
        cert_file="/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt"
    fi
    if [ -f "$cert_file" ]; then
        log_success "Root CA Certificate found: $cert_file"
        echo ""
        echo "Download via Browser: https://homelab.lan/caddy-homelab-root.crt (or https://$IP_HOST/caddy-homelab-root.crt)"
        if command -v openssl >/dev/null 2>&1; then
            echo "Fingerprint (SHA256):"
            openssl x509 -noout -fingerprint -sha256 -in "$cert_file" 2>/dev/null || true
        fi
        echo ""
        echo "Installation instructions for Green HTTPS:"
        echo "  - Windows : Double-click .crt -> 'Install Certificate' -> Local Machine -> 'Trusted Root Certification Authorities'"
        echo "  - macOS   : Keychain Access -> System -> Drag & drop -> 'Always Trust'"
        echo "  - Android : Settings -> Security -> Install Certificate -> CA Certificate"
        echo "  - iOS/iPad: AirDrop / Safari download -> Settings -> Profile Downloaded -> General -> About -> Certificate Trust Settings"
    else
        log_error "Root CA certificate not found at $cert_file"
    fi
}

cmd_mode() {
    check_root
    local new_mode="${1:-status}"
    case "$new_mode" in
        gateway|gw)
            log_info "Switching network mode to Transparent Home Gateway (masquerade + forward active)..."
            if [ -f "$ROOT_DIR/config.env" ]; then
                if grep -q "^GATEWAY_MODE=" "$ROOT_DIR/config.env"; then
                    sed -i 's|^GATEWAY_MODE=.*|GATEWAY_MODE="yes"|' "$ROOT_DIR/config.env"
                else
                    echo 'GATEWAY_MODE="yes"' >> "$ROOT_DIR/config.env"
                fi
            fi
            export GATEWAY_MODE="yes"
            sh "$ROOT_DIR/core/05-firewall.sh"
            log_success "Gateway mode active. Set devices' Default Gateway and DNS to $IP_HOST."
            ;;
        standalone|server|srv)
            log_info "Switching network mode to Standalone Host (forwarding disabled)..."
            if [ -f "$ROOT_DIR/config.env" ]; then
                if grep -q "^GATEWAY_MODE=" "$ROOT_DIR/config.env"; then
                    sed -i 's|^GATEWAY_MODE=.*|GATEWAY_MODE="no"|' "$ROOT_DIR/config.env"
                else
                    echo 'GATEWAY_MODE="no"' >> "$ROOT_DIR/config.env"
                fi
            fi
            export GATEWAY_MODE="no"
            sh "$ROOT_DIR/core/05-firewall.sh"
            log_success "Standalone mode active. Host services only."
            ;;
        status|"")
            echo "Current Gateway Mode: ${GATEWAY_MODE:-yes}"
            if [ "${GATEWAY_MODE:-yes}" = "yes" ]; then
                echo "  Type: Transparent Home Gateway (routes other devices via $IP_HOST)"
            else
                echo "  Type: Standalone Server (services only on this host)"
            fi
            ;;
        *)
            echo "Usage: homelab mode <gateway|standalone|status>"
            ;;
    esac
}

cmd_proxy() {
    check_root
    local input="$*"
    if [ -n "$input" ]; then
        log_info "Updating outbound proxy connection..."
        if [ -f "$ROOT_DIR/config.env" ]; then
            if grep -q "^PROXY_INPUT=" "$ROOT_DIR/config.env"; then
                sed -i "s|^PROXY_INPUT=.*|PROXY_INPUT=\"$input\"|" "$ROOT_DIR/config.env"
            else
                echo "PROXY_INPUT=\"$input\"" >> "$ROOT_DIR/config.env"
            fi
        fi
        /usr/local/bin/update-sub.py "$input"
    else
        log_info "Reloading outbound proxy connection from config.env..."
        /usr/local/bin/update-sub.py
    fi
}

# Main Command Dispatcher
CMD="${1:-help}"
shift || true

case "$CMD" in
    install)
        cmd_install "$@"
        ;;
    reconfigure|config)
        cmd_reconfigure "$@"
        ;;
    update)
        cmd_update "$@"
        ;;
    doctor)
        cmd_doctor "$@"
        ;;
    status)
        cmd_status "$@"
        ;;
    service)
        cmd_service "$@"
        ;;
    summary)
        cmd_summary
        ;;
    backup)
        cmd_backup "$@"
        ;;
    restore)
        cmd_restore "$@"
        ;;
    ip)
        cmd_ip "$@"
        ;;
    cert|ca|ssl)
        cmd_cert "$@"
        ;;
    mode)
        cmd_mode "$@"
        ;;
    tailscale)
        cmd_tailscale "$@"
        ;;
    proxy)
        cmd_proxy "$@"
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        log_error "Unknown command: $CMD"
        usage
        ;;
esac
