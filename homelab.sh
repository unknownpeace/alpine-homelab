#!/bin/sh
set -e

# ==============================================================================
# Alpine Homelab Appliance - CLI Orchestrator
# ==============================================================================

SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
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
    echo "  update             Pull git updates, refresh rules, and restart services"
    echo "  doctor             Run full automated system health & diagnostics audit"
    echo "  status             Show quick summary of running services and resources"
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
    [ "${ENABLE_SINGBOX:-yes}" = "yes" ] && sh "$SERVICES_DIR/sing-box/install.sh"
    [ "${ENABLE_CADDY:-yes}" = "yes" ] && sh "$SERVICES_DIR/caddy/install.sh"
    [ "${ENABLE_VAULTWARDEN:-yes}" = "yes" ] && sh "$SERVICES_DIR/vaultwarden/install.sh"
    [ "${ENABLE_FORGEJO:-yes}" = "yes" ] && sh "$SERVICES_DIR/forgejo/install.sh"
    [ "${ENABLE_CONDUIT:-yes}" = "yes" ] && sh "$SERVICES_DIR/conduit/install.sh"
    [ "${ENABLE_NAVIDROME:-yes}" = "yes" ] && sh "$SERVICES_DIR/navidrome/install.sh"
    [ "${ENABLE_TRANSMISSION:-yes}" = "yes" ] && sh "$SERVICES_DIR/transmission/install.sh"
    [ "${ENABLE_SAMBA:-yes}" = "yes" ] && sh "$SERVICES_DIR/samba/install.sh"
    [ "${ENABLE_YTDLP:-yes}" = "yes" ] && sh "$SERVICES_DIR/ytdlp-web/install.sh"
    [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ] && sh "$SERVICES_DIR/lxc-manager/install.sh"

    # Print Summary Cheat Sheet
    cmd_summary
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
    echo -e "  Файл сертификата : ${C_GREEN}/root/caddy-homelab-root.crt${C_RESET}"
    echo -e "  Скачайте его и установите в «Доверенные корневые центры сертификации» на ПК/Телефоне."
    echo ""
    echo -e "${C_PURPLE}${C_BOLD}3. ДОСТУП К ВЕБ-СЕРВИСАМ (Локальный защищенный HTTPS):${C_RESET}"
    echo -e "  ------------------------------------------------------------------------------"
    echo -e "  ${C_WHITE}Сервис${C_RESET}          | ${C_WHITE}Адрес домена${C_RESET}               | ${C_WHITE}Локальный порт${C_RESET}   | ${C_WHITE}Авторизация${C_RESET}"
    echo -e "  ------------------------------------------------------------------------------"
    [ "${ENABLE_VAULTWARDEN:-yes}" = "yes" ] && echo -e "  Vaultwarden     : ${C_CYAN}https://vault.$BASE_DOMAIN${C_RESET}        127.0.0.1:8000           Встроенная (Web Vault)"
    [ "${ENABLE_FORGEJO:-yes}" = "yes" ]     && echo -e "  Forgejo Git     : ${C_CYAN}https://git.$BASE_DOMAIN${C_RESET}          127.0.0.1:3000           Встроенная (SSH: 2222)"
    [ "${ENABLE_CONDUIT:-yes}" = "yes" ]     && echo -e "  Matrix Conduit  : ${C_CYAN}https://matrix.$BASE_DOMAIN${C_RESET}       127.0.0.1:6167           Токены клиентов Matrix"
    [ "${ENABLE_NAVIDROME:-yes}" = "yes" ]   && echo -e "  Navidrome Music : ${C_CYAN}https://music.$BASE_DOMAIN${C_RESET}        127.0.0.1:4533           Встроенная (Subsonic)"
    [ "${ENABLE_TRANSMISSION:-yes}" = "yes" ] && echo -e "  Transmission    : ${C_CYAN}https://torrent.$BASE_DOMAIN${C_RESET}      127.0.0.1:9091           ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    [ "${ENABLE_YTDLP:-yes}" = "yes" ]       && echo -e "  Media Downloader: ${C_CYAN}https://ytdl.$BASE_DOMAIN${C_RESET}         127.0.0.1:8080           ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    [ "${ENABLE_SINGBOX:-yes}" = "yes" ]     && echo -e "  sing-box UI/DNS : ${C_CYAN}https://proxy.$BASE_DOMAIN/ui/${C_RESET}    127.0.0.1:9090 (DNS: 53) ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}"
    [ "${ENABLE_LXC_MANAGER:-yes}" = "yes" ] && echo -e "  LXC Sandbox Mgr : ${C_CYAN}https://lxc.$BASE_DOMAIN${C_RESET}          127.0.0.1:8888, 7681     ${C_YELLOW}$AUTH_USER / $AUTH_PASS${C_RESET}  (LAN DHCP, Snapshots, ttyd)"
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

    echo ""
    echo -e "${C_WHITE}Checking Network Bridges:${C_RESET}"
    local bridges="lxcbr0"
    [ "${BRIDGE_ENABLE:-yes}" = "yes" ] && bridges="br0 lxcbr0"
    for br in $bridges; do
        if ip link show "$br" >/dev/null 2>&1; then
            echo -e "  [${C_GREEN}PASS${C_RESET}] Bridge $br exists and is UP"
        else
            echo -e "  [${C_RED}FAIL${C_RESET}] Bridge $br missing"
            issues=$((issues + 1))
        fi
    done

    echo ""
    echo -e "${C_WHITE}Checking DNS Resolution (sing-box port 53):${C_RESET}"
    if nslookup "vault.$BASE_DOMAIN" 127.0.0.1 >/dev/null 2>&1; then
        echo -e "  [${C_GREEN}PASS${C_RESET}] vault.$BASE_DOMAIN resolves locally"
    else
        echo -e "  [${C_RED}FAIL${C_RESET}] vault.$BASE_DOMAIN failed to resolve via 127.0.0.1"
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
    for prefix in vault git matrix music lxc; do
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

# Main Command Dispatcher
CMD="${1:-help}"
shift || true

case "$CMD" in
    install)
        cmd_install "$@"
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
    help|--help|-h)
        usage
        ;;
    *)
        log_error "Unknown command: $CMD"
        usage
        ;;
esac
