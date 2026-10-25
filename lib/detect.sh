#!/bin/sh
# ==============================================================================
# Alpine Homelab Appliance - Network, Environment & Interactive Setup Wizard
# ==============================================================================

# 1. Load config.env if present
DETECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(cd "$DETECT_DIR/.." && pwd)"

if [ -f "/opt/homelab/config.env" ]; then
    # shellcheck disable=SC1091
    . "/opt/homelab/config.env"
elif [ -f "$PARENT_DIR/config.env" ]; then
    # shellcheck disable=SC1091
    . "$PARENT_DIR/config.env"
fi

# 2. Network Autodetection
PHYSICAL_IF="${PHYSICAL_IF:-$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}' || echo "eth0")}"
IP_HOST="${IP_HOST:-$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || ip -4 -o addr show dev "$PHYSICAL_IF" 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}' || echo "192.168.1.147")}"
CIDR_PREFIX="${CIDR_PREFIX:-$(ip -4 -o addr show dev "$PHYSICAL_IF" 2>/dev/null | awk '{split($4, a, "/"); print a[2]; exit}')}"
[ -z "$CIDR_PREFIX" ] && CIDR_PREFIX="24"
DEFAULT_GW="${DEFAULT_GW:-$(ip -4 route show default dev "$PHYSICAL_IF" 2>/dev/null | awk '{print $3; exit}')}"
[ -z "$DEFAULT_GW" ] && DEFAULT_GW="$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')"
LAN_NETMASK="${LAN_NETMASK:-255.255.255.0}"

# 3. Interactive First-Run Setup Wizard (Only if config.env does NOT exist and stdin is a TTY)
CONFIG_TARGET="/opt/homelab/config.env"
[ ! -d "/opt/homelab" ] && CONFIG_TARGET="$PARENT_DIR/config.env"

if [ ! -f "$CONFIG_TARGET" ] && [ -t 0 ] && [ -z "${UNATTENDED:-}" ]; then
    echo ""
    echo -e "${C_BLUE}================================================================${C_RESET}"
    echo -e "${C_BLUE}    Alpine Homelab Appliance - Мастер Первоначальной Настройки  ${C_RESET}"
    echo -e "${C_BLUE}================================================================${C_RESET}"
    echo -e "Обнаружена сеть: Интерфейс ${C_CYAN}$PHYSICAL_IF${C_RESET}, IP ${C_YELLOW}$IP_HOST${C_RESET}, Шлюз ${C_GREEN}$DEFAULT_GW${C_RESET}"
    echo ""

    # 3.1 Username
    printf "1. Имя пользователя для входа [Enter = admin]: "
    read -r IN_USER
    AUTH_USER="${IN_USER:-admin}"

    # 3.2 Password
    printf "2. Пароль администратора [Enter = сгенерировать надежный 16-значный]: "
    read -r IN_PASS
    if [ -z "$IN_PASS" ]; then
        AUTH_PASS="$(tr -dc 'a-zA-Z0-9' </dev/urandom 2>/dev/null | head -c 16 || echo "HomelabPass2026")"
        echo -e "   ${C_GREEN}Сгенерирован пароль:${C_RESET} ${C_YELLOW}${C_BOLD}$AUTH_PASS${C_RESET}"
    else
        AUTH_PASS="$IN_PASS"
    fi

    # 3.3 Domain
    printf "3. Базовый домен для локальных HTTPS сайтов (*.lan) [Enter = lan]: "
    read -r IN_DOM
    BASE_DOMAIN="${IN_DOM:-lan}"

    # 3.4 Timezone
    printf "4. Системная таймзона [Enter = Europe/Moscow]: "
    read -r IN_TZ
    TIMEZONE="${IN_TZ:-Europe/Moscow}"

    # 3.5 Storage Base
    printf "5. Путь к основному хранилищу медиа/файлов [Enter = /srv/storage]: "
    read -r IN_STORE
    STORAGE_BASE="${IN_STORE:-/srv/storage}"

    # 3.6 Gateway mode
    echo ""
    echo -e "6. Режим работы шлюза сети:"
    echo -e "   ${C_CYAN}1)${C_RESET} Домашний прозрачный шлюз (для ТВ, ПК, телефонов с обходом блокировок) [По умолчанию]"
    echo -e "   ${C_CYAN}2)${C_RESET} Автономный сервер (только для работы сервисов на этом хосте)"
    printf "   Ваш выбор [1-2, Enter = 1]: "
    read -r IN_GW
    if [ "$IN_GW" = "2" ]; then
        GATEWAY_MODE="no"
    else
        GATEWAY_MODE="yes"
    fi

    # 3.7 Proxy & Anti-censorship
    echo ""
    echo -e "7. Исходящее подключение для обхода блокировок (sing-box):"
    echo -e "   ${C_CYAN}1)${C_RESET} Ввести отдельный сервер (${C_YELLOW}vless://${C_RESET}, ${C_YELLOW}ss://${C_RESET}, ${C_YELLOW}trojan://${C_RESET}, ${C_YELLOW}hy2://${C_RESET})"
    echo -e "   ${C_CYAN}2)${C_RESET} Ввести URL подписки (${C_YELLOW}https://...${C_RESET} sing-box JSON, Clash или Base64)"
    echo -e "   ${C_CYAN}3)${C_RESET} Прямое подключение без прокси (${C_YELLOW}direct${C_RESET}) [По умолчанию]"
    printf "   Ваш выбор [1-3, Enter = 3]: "
    read -r IN_PROXY
    case "$IN_PROXY" in
        1)
            printf "   Вставьте строку подключения узла: "
            read -r USER_NODE
            PROXY_INPUT="$USER_NODE"
            ;;
        2)
            printf "   Вставьте URL подписки: "
            read -r USER_SUB
            PROXY_INPUT="$USER_SUB"
            ;;
        *)
            PROXY_INPUT="direct"
            ;;
    esac
    [ -z "$PROXY_INPUT" ] && PROXY_INPUT="direct"

    # 3.8 Service Stack Selection
    echo ""
    echo -e "8. Набор устанавливаемых сервисов:"
    echo -e "   ${C_CYAN}1)${C_RESET} Полный стек (9 сервисов: Caddy, sing-box, LXC, Vaultwarden, Git, Matrix, Music, Торренты, Samba, yt-dlp) [По умолчанию]"
    echo -e "   ${C_CYAN}2)${C_RESET} Минимальный стек (Caddy + sing-box + LXC Sandbox, экономия RAM)"
    printf "   Ваш выбор [1-2, Enter = 1]: "
    read -r IN_STACK
    if [ "$IN_STACK" = "2" ]; then
        ENABLE_CADDY="yes"
        ENABLE_SINGBOX="yes"
        ENABLE_LXC_MANAGER="yes"
        ENABLE_VAULTWARDEN="no"
        ENABLE_FORGEJO="no"
        ENABLE_CONDUIT="no"
        ENABLE_NAVIDROME="no"
        ENABLE_TRANSMISSION="no"
        ENABLE_SAMBA="no"
        ENABLE_YTDLP="no"
    else
        ENABLE_CADDY="yes"
        ENABLE_SINGBOX="yes"
        ENABLE_LXC_MANAGER="yes"
        ENABLE_VAULTWARDEN="yes"
        ENABLE_FORGEJO="yes"
        ENABLE_CONDUIT="yes"
        ENABLE_NAVIDROME="yes"
        ENABLE_TRANSMISSION="yes"
        ENABLE_SAMBA="yes"
        ENABLE_YTDLP="yes"
    fi

    # 3.9 Save answers to config.env
    mkdir -p "$(dirname "$CONFIG_TARGET")"
    cat << EOF > "$CONFIG_TARGET"
# Generated by Alpine Homelab Setup Wizard on $(date)
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
ENABLE_SINGBOX="$ENABLE_SINGBOX"
ENABLE_LXC_MANAGER="$ENABLE_LXC_MANAGER"
ENABLE_VAULTWARDEN="$ENABLE_VAULTWARDEN"
ENABLE_FORGEJO="$ENABLE_FORGEJO"
ENABLE_CONDUIT="$ENABLE_CONDUIT"
ENABLE_NAVIDROME="$ENABLE_NAVIDROME"
ENABLE_TRANSMISSION="$ENABLE_TRANSMISSION"
ENABLE_SAMBA="$ENABLE_SAMBA"
ENABLE_YTDLP="$ENABLE_YTDLP"
EOF
    chmod 600 "$CONFIG_TARGET"
    echo ""
    log_success "Настройки сохранены в $CONFIG_TARGET (chmod 600)."
    echo ""
fi

# 4. Final Fallbacks & Defaults (if not set by config.env or wizard)
AUTH_USER="${AUTH_USER:-admin}"
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
ENABLE_SINGBOX="${ENABLE_SINGBOX:-yes}"
ENABLE_LXC_MANAGER="${ENABLE_LXC_MANAGER:-yes}"
ENABLE_VAULTWARDEN="${ENABLE_VAULTWARDEN:-yes}"
ENABLE_FORGEJO="${ENABLE_FORGEJO:-yes}"
ENABLE_CONDUIT="${ENABLE_CONDUIT:-yes}"
ENABLE_NAVIDROME="${ENABLE_NAVIDROME:-yes}"
ENABLE_TRANSMISSION="${ENABLE_TRANSMISSION:-yes}"
ENABLE_SAMBA="${ENABLE_SAMBA:-yes}"
ENABLE_YTDLP="${ENABLE_YTDLP:-yes}"
