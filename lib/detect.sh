#!/bin/sh
# ==============================================================================
# Alpine Homelab Appliance - Network, Environment & Interactive Setup Wizard
# ==============================================================================

# 1. Load config.env if present (or backup config.env.bak during reconfigure)
DETECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(cd "$DETECT_DIR/.." && pwd)"

CONFIG_TARGET="/opt/homelab/config.env"
[ ! -d "/opt/homelab" ] && CONFIG_TARGET="$PARENT_DIR/config.env"

if [ -f "$CONFIG_TARGET" ]; then
    # shellcheck disable=SC1090
    . "$CONFIG_TARGET"
elif [ -f "${CONFIG_TARGET}.bak" ]; then
    # shellcheck disable=SC1090
    . "${CONFIG_TARGET}.bak"
fi

# 2. Network Autodetection
detect_lan_if() {
    if ip link show dev br0 >/dev/null 2>&1; then
        echo "br0"
        return
    fi
    local cand
    cand=$(ip -4 route show 2>/dev/null | grep -v 'tun' | grep -v 'lxc' | awk '/default/ {print $5; exit}')
    [ -n "$cand" ] && echo "$cand" && return
    cand=$(ip -o link show 2>/dev/null | awk -F': ' '{print $2}' | grep -E '^(eth|en|wlan)' | head -n1)
    echo "${cand:-eth0}"
}

detect_lan_ip() {
    local iface="$1"
    local ip
    ip=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}')
    [ -n "$ip" ] && echo "$ip" && return
    ip=$(ip -4 -o addr show 2>/dev/null | grep -v '127.0.0.1' | grep -v 'tun' | grep -v 'lxcbr' | awk '{split($4, a, "/"); print a[1]; exit}')
    echo "${ip:-192.168.1.147}"
}

PHYSICAL_IF="${PHYSICAL_IF:-$(detect_lan_if)}"
IP_HOST="${IP_HOST:-$(detect_lan_ip "$PHYSICAL_IF")}"
CIDR_PREFIX="${CIDR_PREFIX:-$(ip -4 -o addr show dev "$PHYSICAL_IF" 2>/dev/null | awk '{split($4, a, "/"); print a[2]; exit}')}"
[ -z "$CIDR_PREFIX" ] && CIDR_PREFIX="24"
DEFAULT_GW="${DEFAULT_GW:-$(ip -4 route show default dev "$PHYSICAL_IF" 2>/dev/null | awk '{print $3; exit}')}"
[ -z "$DEFAULT_GW" ] && DEFAULT_GW="$(ip -4 route show 2>/dev/null | grep -v 'tun' | grep -v 'lxc' | awk '/default/ {print $3; exit}')"
[ -z "$DEFAULT_GW" ] && DEFAULT_GW="$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')"
LAN_NETMASK="${LAN_NETMASK:-255.255.255.0}"

# 3. Interactive Setup Wizard (Runs on first install, or when explicitly requested via 'homelab reconfigure')
CONFIG_TARGET="/opt/homelab/config.env"
[ ! -d "/opt/homelab" ] && CONFIG_TARGET="$PARENT_DIR/config.env"

# Preserve previously saved settings as smart defaults
PREV_AUTH_USER="${AUTH_USER:-admin}"
PREV_AUTH_PASS="${AUTH_PASS:-}"
PREV_BASE_DOMAIN="${BASE_DOMAIN:-lan}"
PREV_TIMEZONE="${TIMEZONE:-Europe/Moscow}"
PREV_STORAGE_BASE="${STORAGE_BASE:-/srv/storage}"
PREV_GATEWAY_MODE="${GATEWAY_MODE:-yes}"
PREV_PROXY_INPUT="${PROXY_INPUT:-direct}"
PREV_ENABLE_VAULTWARDEN="${ENABLE_VAULTWARDEN:-no}"
PREV_ENABLE_FORGEJO="${ENABLE_FORGEJO:-no}"
PREV_ENABLE_CONDUIT="${ENABLE_CONDUIT:-no}"
PREV_ENABLE_NAVIDROME="${ENABLE_NAVIDROME:-no}"
PREV_ENABLE_TRANSMISSION="${ENABLE_TRANSMISSION:-no}"
PREV_ENABLE_YTDLP="${ENABLE_YTDLP:-no}"
PREV_ENABLE_SAMBA="${ENABLE_SAMBA:-no}"
PREV_ENABLE_LXC_MANAGER="${ENABLE_LXC_MANAGER:-no}"
PREV_ENABLE_ADGUARD_HOME="${ENABLE_ADGUARD_HOME:-no}"
PREV_ENABLE_TAILSCALE="${ENABLE_TAILSCALE:-no}"

if { [ ! -f "$CONFIG_TARGET" ] || [ "${RECONFIG_MODE:-0}" = "1" ]; } && [ -t 0 ] && [ -z "${UNATTENDED:-}" ]; then
    echo ""
    echo -e "${C_BLUE}================================================================${C_RESET}"
    echo -e "${C_BLUE}    Alpine Homelab Appliance - Мастер Настройки                 ${C_RESET}"
    echo -e "${C_BLUE}================================================================${C_RESET}"
    echo -e "Обнаружена сеть: Интерфейс ${C_CYAN}$PHYSICAL_IF${C_RESET}, IP ${C_YELLOW}$IP_HOST${C_RESET}, Шлюз ${C_GREEN}$DEFAULT_GW${C_RESET}"
    echo ""

    # 3.1 Username (Sanitized to lowercase ASCII alphanumeric)
    printf "1. Имя пользователя для входа [Enter = %s]: " "${PREV_AUTH_USER:-admin}"
    read -r IN_USER
    IN_USER="${IN_USER:-$PREV_AUTH_USER}"
    CLEAN_USER=$(echo "$IN_USER" | tr -cd 'a-zA-Z0-9_-' | tr '[:upper:]' '[:lower:]')
    [ -z "$CLEAN_USER" ] && CLEAN_USER="admin"
    if [ "$IN_USER" != "$CLEAN_USER" ]; then
        echo -e "   ${C_YELLOW}[!] Предупреждение: имя пользователя исправлено на латиницу:${C_RESET} ${C_BOLD}$CLEAN_USER${C_RESET}"
    fi
    AUTH_USER="$CLEAN_USER"

    # 3.2 Password
    if [ -n "$PREV_AUTH_PASS" ]; then
        printf "2. Пароль администратора [Enter = сохранить текущий]: "
        read -r IN_PASS
        if [ -z "$IN_PASS" ]; then
            AUTH_PASS="$PREV_AUTH_PASS"
            echo -e "   ${C_GREEN}Сохранен текущий пароль администратора.${C_RESET}"
        else
            AUTH_PASS="$IN_PASS"
        fi
    else
        printf "2. Пароль администратора [Enter = сгенерировать надежный 16-значный]: "
        read -r IN_PASS
        if [ -z "$IN_PASS" ]; then
            AUTH_PASS="$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 16 || echo "HomelabPass2026")"
            echo -e "   ${C_GREEN}Сгенерирован пароль:${C_RESET} ${C_YELLOW}${C_BOLD}$AUTH_PASS${C_RESET}"
        else
            AUTH_PASS="$IN_PASS"
        fi
    fi

    # 3.3 Domain
    printf "3. Базовый домен для локальных HTTPS сайтов (*.lan) [Enter = %s]: " "${PREV_BASE_DOMAIN:-lan}"
    read -r IN_DOM
    BASE_DOMAIN="${IN_DOM:-${PREV_BASE_DOMAIN:-lan}}"

    # 3.4 Timezone
    printf "4. Системная таймзона [Enter = %s]: " "${PREV_TIMEZONE:-Europe/Moscow}"
    read -r IN_TZ
    TIMEZONE="${IN_TZ:-${PREV_TIMEZONE:-Europe/Moscow}}"

    # 3.5 Storage Base
    printf "5. Путь к основному хранилищу медиа/файлов [Enter = %s]: " "${PREV_STORAGE_BASE:-/srv/storage}"
    read -r IN_STORE
    STORAGE_BASE="${IN_STORE:-${PREV_STORAGE_BASE:-/srv/storage}}"

    # 3.6 Gateway mode
    echo ""
    echo -e "6. Режим работы шлюза сети:"
    gw_def="1"
    [ "$PREV_GATEWAY_MODE" = "no" ] && gw_def="2"
    echo -e "   ${C_CYAN}1)${C_RESET} Домашний прозрачный шлюз (для ТВ, ПК, телефонов с обходом блокировок)"
    echo -e "   ${C_CYAN}2)${C_RESET} Автономный сервер (только для работы сервисов на этом хосте)"
    printf "   Ваш выбор [1-2, Enter = %s]: " "$gw_def"
    read -r IN_GW
    IN_GW="${IN_GW:-$gw_def}"
    if [ "$IN_GW" = "2" ]; then
        GATEWAY_MODE="no"
    else
        GATEWAY_MODE="yes"
    fi

    # 3.7 Proxy & Anti-censorship
    echo ""
    echo -e "7. Исходящее подключение для обхода блокировок (sing-box):"
    if [ -n "$PREV_PROXY_INPUT" ] && [ "$PREV_PROXY_INPUT" != "direct" ]; then
        short_proxy=$(echo "$PREV_PROXY_INPUT" | cut -c1-65)
        echo -e "   ${C_GREEN}0)${C_RESET} ${C_BOLD}Сохранить текущее подключение:${C_RESET} ${C_YELLOW}$short_proxy...${C_RESET} ${C_GREEN}[По умолчанию]${C_RESET}"
        echo -e "   ${C_CYAN}1)${C_RESET} Ввести отдельный сервер (${C_YELLOW}vless://${C_RESET}, ${C_YELLOW}ss://${C_RESET}, ${C_YELLOW}trojan://${C_RESET}, ${C_YELLOW}hy2://${C_RESET})"
        echo -e "   ${C_CYAN}2)${C_RESET} Ввести URL подписки (${C_YELLOW}https://...${C_RESET} sing-box JSON, Clash или Base64)"
        echo -e "   ${C_CYAN}3)${C_RESET} Прямое подключение без прокси (${C_YELLOW}direct${C_RESET})"
        printf "   Ваш выбор [0-3, Enter = 0]: "
        read -r IN_PROXY
        IN_PROXY="${IN_PROXY:-0}"
    else
        echo -e "   ${C_CYAN}1)${C_RESET} Ввести отдельный сервер (${C_YELLOW}vless://${C_RESET}, ${C_YELLOW}ss://${C_RESET}, ${C_YELLOW}trojan://${C_RESET}, ${C_YELLOW}hy2://${C_RESET})"
        echo -e "   ${C_CYAN}2)${C_RESET} Ввести URL подписки (${C_YELLOW}https://...${C_RESET} sing-box JSON, Clash или Base64)"
        echo -e "   ${C_CYAN}3)${C_RESET} Прямое подключение без прокси (${C_YELLOW}direct${C_RESET}) [По умолчанию]"
        printf "   Ваш выбор [1-3, Enter = 3]: "
        read -r IN_PROXY
        IN_PROXY="${IN_PROXY:-3}"
    fi
    case "$IN_PROXY" in
        vless://*|ss://*|trojan://*|hy2://*|hysteria2://*|vmess://*|http://*|https://*)
            PROXY_INPUT="$IN_PROXY"
            echo -e "   ${C_GREEN}[✓] Обнаружена прямая строка подключения или URL подписки.${C_RESET}"
            ;;
        0)
            PROXY_INPUT="$PREV_PROXY_INPUT"
            ;;
        1)
            printf "   Вставьте строку подключения узла: "
            read -r USER_NODE
            PROXY_INPUT="${USER_NODE:-$PREV_PROXY_INPUT}"
            ;;
        2)
            printf "   Вставьте URL подписки: "
            read -r USER_SUB
            PROXY_INPUT="${USER_SUB:-$PREV_PROXY_INPUT}"
            ;;
        *)
            PROXY_INPUT="direct"
            ;;
    esac
    [ -z "$PROXY_INPUT" ] && PROXY_INPUT="direct"

    # 3.8 Service Stack Selection
    curr_svcs=""
    [ "$PREV_ENABLE_VAULTWARDEN" = "yes" ] && curr_svcs="${curr_svcs}Vaultwarden, "
    [ "$PREV_ENABLE_FORGEJO" = "yes" ] && curr_svcs="${curr_svcs}Forgejo, "
    [ "$PREV_ENABLE_CONDUIT" = "yes" ] && curr_svcs="${curr_svcs}Matrix, "
    [ "$PREV_ENABLE_NAVIDROME" = "yes" ] && curr_svcs="${curr_svcs}Navidrome, "
    [ "$PREV_ENABLE_TRANSMISSION" = "yes" ] && curr_svcs="${curr_svcs}Transmission, "
    [ "$PREV_ENABLE_YTDLP" = "yes" ] && curr_svcs="${curr_svcs}MediaDownloader, "
    [ "$PREV_ENABLE_SAMBA" = "yes" ] && curr_svcs="${curr_svcs}Samba, "
    [ "$PREV_ENABLE_LXC_MANAGER" = "yes" ] && curr_svcs="${curr_svcs}LXC, "
    [ "$PREV_ENABLE_ADGUARD_HOME" = "yes" ] && curr_svcs="${curr_svcs}AdGuardHome, "
    [ "$PREV_ENABLE_TAILSCALE" = "yes" ] && curr_svcs="${curr_svcs}Tailscale, "
    curr_svcs=$(echo "$curr_svcs" | sed 's/, $//')

    curr_nums=""
    [ "$PREV_ENABLE_VAULTWARDEN" = "yes" ] && curr_nums="${curr_nums}1,"
    [ "$PREV_ENABLE_FORGEJO" = "yes" ] && curr_nums="${curr_nums}2,"
    [ "$PREV_ENABLE_CONDUIT" = "yes" ] && curr_nums="${curr_nums}3,"
    [ "$PREV_ENABLE_NAVIDROME" = "yes" ] && curr_nums="${curr_nums}4,"
    [ "$PREV_ENABLE_TRANSMISSION" = "yes" ] && curr_nums="${curr_nums}5,"
    [ "$PREV_ENABLE_YTDLP" = "yes" ] && curr_nums="${curr_nums}6,"
    [ "$PREV_ENABLE_SAMBA" = "yes" ] && curr_nums="${curr_nums}7,"
    [ "$PREV_ENABLE_LXC_MANAGER" = "yes" ] && curr_nums="${curr_nums}8,"
    [ "$PREV_ENABLE_ADGUARD_HOME" = "yes" ] && curr_nums="${curr_nums}9,"
    [ "$PREV_ENABLE_TAILSCALE" = "yes" ] && curr_nums="${curr_nums}10,"
    curr_nums=$(echo "$curr_nums" | sed 's/,$//')

    echo ""
    echo -e "8. Набор устанавливаемых сервисов:"
    if [ -n "$curr_svcs" ]; then
        echo -e "   ${C_GREEN}0)${C_RESET} ${C_BOLD}Сохранить текущий набор:${C_RESET} ${C_YELLOW}$curr_svcs${C_RESET} ${C_GREEN}[По умолчанию]${C_RESET}"
        echo -e "   ${C_CYAN}1)${C_RESET} Полный стек (9 сервисов + LXC, «всё включено», ~450 МБ RAM)"
        echo -e "   ${C_CYAN}2)${C_RESET} Чистый сетевой шлюз (Caddy + sing-box, экономия RAM, ~45 МБ, без контейнеров)"
        echo -e "   ${C_CYAN}3)${C_RESET} Шлюз + LXC Контейнеры (Caddy + sing-box + LXC Sandbox, ~150 МБ RAM)"
        echo -e "   ${C_CYAN}4)${C_RESET} Выборочная установка (выбрать конкретные сервисы вручную)"
        printf "   Ваш выбор [0-4, Enter = 0]: "
        read -r IN_STACK
        IN_STACK="${IN_STACK:-0}"
    else
        echo -e "   ${C_CYAN}1)${C_RESET} Полный стек (9 сервисов + LXC, «всё включено», ~450 МБ RAM) [По умолчанию]"
        echo -e "   ${C_CYAN}2)${C_RESET} Чистый сетевой шлюз (Caddy + sing-box, экономия RAM, ~45 МБ, без контейнеров)"
        echo -e "   ${C_CYAN}3)${C_RESET} Шлюз + LXC Контейнеры (Caddy + sing-box + LXC Sandbox, ~150 МБ RAM)"
        echo -e "   ${C_CYAN}4)${C_RESET} Выборочная установка (выбрать конкретные сервисы вручную)"
        printf "   Ваш выбор [1-4, Enter = 1]: "
        read -r IN_STACK
        IN_STACK="${IN_STACK:-1}"
    fi

    case "$IN_STACK" in
        0)
            ENABLE_CADDY="yes"
            ENABLE_SINGBOX="yes"
            ENABLE_DASHBOARD="yes"
            ENABLE_LXC_MANAGER="${PREV_ENABLE_LXC_MANAGER:-no}"
            ENABLE_VAULTWARDEN="${PREV_ENABLE_VAULTWARDEN:-no}"
            ENABLE_FORGEJO="${PREV_ENABLE_FORGEJO:-no}"
            ENABLE_CONDUIT="${PREV_ENABLE_CONDUIT:-no}"
            ENABLE_NAVIDROME="${PREV_ENABLE_NAVIDROME:-no}"
            ENABLE_TRANSMISSION="${PREV_ENABLE_TRANSMISSION:-no}"
            ENABLE_SAMBA="${PREV_ENABLE_SAMBA:-no}"
            ENABLE_YTDLP="${PREV_ENABLE_YTDLP:-no}"
            ENABLE_ADGUARD_HOME="${PREV_ENABLE_ADGUARD_HOME:-no}"
            ENABLE_TAILSCALE="${PREV_ENABLE_TAILSCALE:-no}"
            ;;
        2)
            ENABLE_CADDY="yes"
            ENABLE_SINGBOX="yes"
            ENABLE_DASHBOARD="yes"
            ENABLE_LXC_MANAGER="no"
            ENABLE_VAULTWARDEN="no"
            ENABLE_FORGEJO="no"
            ENABLE_CONDUIT="no"
            ENABLE_NAVIDROME="no"
            ENABLE_TRANSMISSION="no"
            ENABLE_SAMBA="no"
            ENABLE_YTDLP="no"
            ENABLE_ADGUARD_HOME="no"
            ENABLE_TAILSCALE="no"
            ;;
        3)
            ENABLE_CADDY="yes"
            ENABLE_SINGBOX="yes"
            ENABLE_DASHBOARD="yes"
            ENABLE_LXC_MANAGER="yes"
            ENABLE_VAULTWARDEN="no"
            ENABLE_FORGEJO="no"
            ENABLE_CONDUIT="no"
            ENABLE_NAVIDROME="no"
            ENABLE_TRANSMISSION="no"
            ENABLE_SAMBA="no"
            ENABLE_YTDLP="no"
            ENABLE_ADGUARD_HOME="no"
            ENABLE_TAILSCALE="no"
            ;;
        4)
            ENABLE_CADDY="yes"
            ENABLE_SINGBOX="yes"
            ENABLE_DASHBOARD="yes"
            ENABLE_LXC_MANAGER="no"
            ENABLE_VAULTWARDEN="no"
            ENABLE_FORGEJO="no"
            ENABLE_CONDUIT="no"
            ENABLE_NAVIDROME="no"
            ENABLE_TRANSMISSION="no"
            ENABLE_SAMBA="no"
            ENABLE_YTDLP="no"
            ENABLE_ADGUARD_HOME="no"
            ENABLE_TAILSCALE="no"

            echo ""
            echo -e "${C_CYAN}--- Выборочная настройка сервисов ---${C_RESET}"
            echo -e "   ${C_WHITE}1)${C_RESET} Vaultwarden        (Менеджер паролей Bitwarden, SQLite)"
            echo -e "   ${C_WHITE}2)${C_RESET} Forgejo Git        (Персональный Git-сервер, SSH: 2222)"
            echo -e "   ${C_WHITE}3)${C_RESET} Conduit Matrix     (Сервер приватного мессенджера Matrix)"
            echo -e "   ${C_WHITE}4)${C_RESET} Navidrome Music    (Музыкальный стриминг-сервер, Subsonic)"
            echo -e "   ${C_WHITE}5)${C_RESET} Transmission       (Торрент-клиент BitTorrent)"
            echo -e "   ${C_WHITE}6)${C_RESET} Media Downloader   (yt-dlp веб-интерфейс скачивания медиа)"
            echo -e "   ${C_WHITE}7)${C_RESET} Samba / WSDD       (Сетевые папки Windows/Mac в проводнике)"
            echo -e "   ${C_WHITE}8)${C_RESET} LXC Sandbox        (Менеджер контейнеров и веб-терминал ttyd)"
            echo -e "   ${C_WHITE}9)${C_RESET} AdGuard Home       (DNS блокировщик рекламы и трекеров)"
            echo -e "  ${C_WHITE}10)${C_RESET} Tailscale VPN      (Удаленный доступ Mesh VPN без белого IP)"
            echo ""
            if [ -n "$curr_nums" ]; then
                printf "   Введите номера через запятую (напр. 1,5,8) или Enter для сохранения [%s]: " "$curr_nums"
                read -r IN_CUSTOM
                IN_CUSTOM="${IN_CUSTOM:-$curr_nums}"
            else
                printf "   Введите номера через запятую (напр. 1,5,8) или нажмите Enter для пошагового выбора: "
                read -r IN_CUSTOM
            fi

            ask_svc() {
                local var_name="$1"
                local prompt="$2"
                local prev_val="${3:-yes}"
                local def_str="Y/n"
                [ "$prev_val" = "no" ] && def_str="y/N"
                local ans
                printf "   %s [%s]: " "$prompt" "$def_str"
                read -r ans
                case "$ans" in
                    [yY]|[yY][eE][sS]|[дД]|[дД][аА]|1) eval "$var_name=yes" ;;
                    [nN]|[nN][oO]|[нН]|[нН][еЕ][тТ]|0) eval "$var_name=no" ;;
                    "") eval "$var_name=\"$prev_val\"" ;;
                    *) eval "$var_name=\"$prev_val\"" ;;
                esac
            }

            case "$IN_CUSTOM" in
                ""|[yY]|[yY][eE][sS]|[дД]|[дД][аА])
                    echo ""
                    echo -e "   ${C_CYAN}Пошаговый выбор сервисов (Enter = значение по умолчанию):${C_RESET}"
                    ask_svc ENABLE_VAULTWARDEN "1. Установить Vaultwarden (пароли Bitwarden)?" "${PREV_ENABLE_VAULTWARDEN:-yes}"
                    ask_svc ENABLE_FORGEJO "2. Установить Forgejo (Git-сервер)?" "${PREV_ENABLE_FORGEJO:-yes}"
                    ask_svc ENABLE_CONDUIT "3. Установить Conduit (Matrix мессенджер)?" "${PREV_ENABLE_CONDUIT:-yes}"
                    ask_svc ENABLE_NAVIDROME "4. Установить Navidrome (Музыка)?" "${PREV_ENABLE_NAVIDROME:-yes}"
                    ask_svc ENABLE_TRANSMISSION "5. Установить Transmission (Торренты)?" "${PREV_ENABLE_TRANSMISSION:-yes}"
                    ask_svc ENABLE_YTDLP "6. Установить Media Downloader (yt-dlp)?" "${PREV_ENABLE_YTDLP:-yes}"
                    ask_svc ENABLE_SAMBA "7. Установить Samba / WSDD (Сетевые папки)?" "${PREV_ENABLE_SAMBA:-yes}"
                    ask_svc ENABLE_LXC_MANAGER "8. Установить LXC Sandbox (Контейнеры)?" "${PREV_ENABLE_LXC_MANAGER:-yes}"
                    ask_svc ENABLE_ADGUARD_HOME "9. Установить AdGuard Home (Блокировка рекламы)?" "${PREV_ENABLE_ADGUARD_HOME:-no}"
                    ask_svc ENABLE_TAILSCALE "10. Установить Tailscale Mesh VPN?" "${PREV_ENABLE_TAILSCALE:-no}"
                    ;;
                all|ALL|all*|все|Все|ВСЕ)
                    ENABLE_VAULTWARDEN="yes"
                    ENABLE_FORGEJO="yes"
                    ENABLE_CONDUIT="yes"
                    ENABLE_NAVIDROME="yes"
                    ENABLE_TRANSMISSION="yes"
                    ENABLE_YTDLP="yes"
                    ENABLE_SAMBA="yes"
                    ENABLE_LXC_MANAGER="yes"
                    ENABLE_ADGUARD_HOME="no"
                    ENABLE_TAILSCALE="no"
                    ;;
                *)
                    for num in $(echo "$IN_CUSTOM" | tr ',' ' '); do
                        case "$num" in
                            1) ENABLE_VAULTWARDEN="yes" ;;
                            2) ENABLE_FORGEJO="yes" ;;
                            3) ENABLE_CONDUIT="yes" ;;
                            4) ENABLE_NAVIDROME="yes" ;;
                            5) ENABLE_TRANSMISSION="yes" ;;
                            6) ENABLE_YTDLP="yes" ;;
                            7) ENABLE_SAMBA="yes" ;;
                            8) ENABLE_LXC_MANAGER="yes" ;;
                            9) ENABLE_ADGUARD_HOME="yes" ;;
                            10) ENABLE_TAILSCALE="yes" ;;
                        esac
                    done
                    ;;
            esac
            ;;
        *)
            ENABLE_CADDY="yes"
            ENABLE_SINGBOX="yes"
            ENABLE_DASHBOARD="yes"
            ENABLE_LXC_MANAGER="yes"
            ENABLE_VAULTWARDEN="yes"
            ENABLE_FORGEJO="yes"
            ENABLE_CONDUIT="yes"
            ENABLE_NAVIDROME="yes"
            ENABLE_TRANSMISSION="yes"
            ENABLE_SAMBA="yes"
            ENABLE_YTDLP="yes"
            ENABLE_ADGUARD_HOME="no"
            ENABLE_TAILSCALE="no"
            ;;
    esac

    # 3.9 Save answers to config.env
    mkdir -p "$(dirname "$CONFIG_TARGET")"
    cat << EOF > "$CONFIG_TARGET"
# Generated by Alpine Homelab Setup Wizard on $(date)
IP_HOST="$IP_HOST"
AUTH_USER="$AUTH_USER"
AUTH_PASS="$AUTH_PASS"
BASE_DOMAIN="$BASE_DOMAIN"
TIMEZONE="$TIMEZONE"
STORAGE_BASE="$STORAGE_BASE"
GATEWAY_MODE="$GATEWAY_MODE"
BRIDGE_ENABLE="yes"
DNS_UPSTREAM_PRIMARY="https://dns.adguard-dns.com/dns-query"
DNS_UPSTREAM_SECONDARY="1.1.1.1"
PROXY_INPUT="$PROXY_INPUT"
MINIROOTFS_URL="https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/alpine-minirootfs-3.24.0-x86_64.tar.gz"

ENABLE_CADDY="$ENABLE_CADDY"
ENABLE_DASHBOARD="${ENABLE_DASHBOARD:-yes}"
ENABLE_SINGBOX="$ENABLE_SINGBOX"
ENABLE_LXC_MANAGER="$ENABLE_LXC_MANAGER"
ENABLE_VAULTWARDEN="$ENABLE_VAULTWARDEN"
ENABLE_FORGEJO="$ENABLE_FORGEJO"
ENABLE_CONDUIT="$ENABLE_CONDUIT"
ENABLE_NAVIDROME="$ENABLE_NAVIDROME"
ENABLE_TRANSMISSION="$ENABLE_TRANSMISSION"
ENABLE_SAMBA="$ENABLE_SAMBA"
ENABLE_YTDLP="$ENABLE_YTDLP"
ENABLE_ADGUARD_HOME="${ENABLE_ADGUARD_HOME:-no}"
ENABLE_TAILSCALE="${ENABLE_TAILSCALE:-no}"
EOF
    chmod 600 "$CONFIG_TARGET"
    echo ""
    log_success "Настройки сохранены в $CONFIG_TARGET (chmod 600)."
    echo ""
fi

# 4. Final Fallbacks & Defaults (if not set by config.env or wizard)
AUTH_USER="${AUTH_USER:-admin}"
CLEAN_USER=$(echo "$AUTH_USER" | tr -cd 'a-zA-Z0-9_-' | tr '[:upper:]' '[:lower:]')
[ -z "$CLEAN_USER" ] && CLEAN_USER="admin"
AUTH_USER="$CLEAN_USER"

AUTH_PASS="${AUTH_PASS:-admin}"
BASE_DOMAIN="${BASE_DOMAIN:-lan}"
TIMEZONE="${TIMEZONE:-Europe/Moscow}"
STORAGE_BASE="${STORAGE_BASE:-/srv/storage}"
GATEWAY_MODE="${GATEWAY_MODE:-yes}"
BRIDGE_ENABLE="${BRIDGE_ENABLE:-yes}"
DNS_UPSTREAM_PRIMARY="${DNS_UPSTREAM_PRIMARY:-https://dns.adguard-dns.com/dns-query}"
DNS_UPSTREAM_SECONDARY="${DNS_UPSTREAM_SECONDARY:-1.1.1.1}"
PROXY_INPUT="${PROXY_INPUT:-direct}"
SUB_URL="$PROXY_INPUT"
MINIROOTFS_URL="${MINIROOTFS_URL:-https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/alpine-minirootfs-3.24.0-x86_64.tar.gz}"

ENABLE_CADDY="${ENABLE_CADDY:-yes}"
ENABLE_DASHBOARD="${ENABLE_DASHBOARD:-yes}"
ENABLE_SINGBOX="${ENABLE_SINGBOX:-yes}"
ENABLE_LXC_MANAGER="${ENABLE_LXC_MANAGER:-yes}"
ENABLE_VAULTWARDEN="${ENABLE_VAULTWARDEN:-yes}"
ENABLE_FORGEJO="${ENABLE_FORGEJO:-yes}"
ENABLE_CONDUIT="${ENABLE_CONDUIT:-yes}"
ENABLE_NAVIDROME="${ENABLE_NAVIDROME:-yes}"
ENABLE_TRANSMISSION="${ENABLE_TRANSMISSION:-yes}"
ENABLE_SAMBA="${ENABLE_SAMBA:-yes}"
ENABLE_YTDLP="${ENABLE_YTDLP:-yes}"
ENABLE_ADGUARD_HOME="${ENABLE_ADGUARD_HOME:-no}"
ENABLE_TAILSCALE="${ENABLE_TAILSCALE:-no}"
