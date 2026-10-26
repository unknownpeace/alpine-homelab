#!/bin/sh
# Shared utilities and helpers for Alpine Homelab CLI

export LC_ALL="C.UTF-8"
export LANG="C.UTF-8"

SAFE_USER=$(echo "${AUTH_USER:-admin}" | tr -cd 'a-zA-Z0-9_-' | tr '[:upper:]' '[:lower:]')
[ -z "$SAFE_USER" ] && SAFE_USER="admin"
export SAFE_USER

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_RED="\033[1;31m"
C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"
C_BLUE="\033[1;34m"
C_PURPLE="\033[1;35m"
C_CYAN="\033[1;36m"
C_WHITE="\033[1;37m"

log_info()    { echo -e "${C_CYAN}[INFO]${C_RESET} $*"; }
log_success() { echo -e "${C_GREEN}[OK]${C_RESET} $*"; }
log_warn()    { echo -e "${C_YELLOW}[WARN]${C_RESET} $*"; }
log_error()   { echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2; }

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "This script must be executed as root."
        exit 1
    fi
}

apk_add() {
    local attempt=1
    local max_attempts=3
    until apk add --no-cache "$@"; do
        if [ "$attempt" -ge "$max_attempts" ]; then
            log_error "Failed to install packages after $max_attempts attempts: $*"
            return 1
        fi
        log_warn "Transient error during apk add (attempt $attempt/$max_attempts). Retrying in 2s..."
        sleep 2
        attempt=$((attempt + 1))
    done
}

deploy_caddy_snippet() {
    local svc="$1"
    local snippet="${2:-$SERVICE_DIR/caddy.snippet}"
    if [ ! -f "$snippet" ]; then
        return 0
    fi
    mkdir -p /etc/caddy/conf.d
    sed -e "s/__BASE_DOMAIN__/${BASE_DOMAIN:-lan}/g" \
        -e "s/import security_headers/import common_optimizations/g" \
        "$snippet" > "/etc/caddy/conf.d/${svc}.conf"
    if rc-service caddy status >/dev/null 2>&1; then
        rc-service caddy reload >/dev/null 2>&1 || rc-service caddy restart >/dev/null 2>&1 || true
    fi
}

remove_caddy_snippet() {
    local svc="$1"
    rm -f "/etc/caddy/conf.d/${svc}.conf"
    if rc-service caddy status >/dev/null 2>&1; then
        rc-service caddy reload >/dev/null 2>&1 || rc-service caddy restart >/dev/null 2>&1 || true
    fi
}
