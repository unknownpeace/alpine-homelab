#!/bin/sh
# Zero-Docker Alpine Homelab - Rapid Endpoint Health Check
for p in /opt/homelab/config.env "$(dirname "$0")/../config.env"; do
    if [ -f "$p" ]; then
        # shellcheck disable=SC1090
        . "$p"
        break
    fi
done
AUTH_USER="${AUTH_USER:-admin}"
AUTH_PASS="${AUTH_PASS:-admin}"
BASE_DOMAIN="${BASE_DOMAIN:-lan}"
HOST_IP="${IP_HOST:-127.0.0.1}"

echo "=== DNS RESOLUTION ==="
for prefix in vault git matrix music torrent ytdl proxy lxc adguard; do
    d="$prefix.$BASE_DOMAIN"
    ip=$(nslookup "$d" 127.0.0.1 2>/dev/null | grep -A1 "Name:" | grep "Address:" | awk '{print $2}')
    echo "$d -> $ip"
done

echo "=== HTTPS ENDPOINTS ==="
for prefix in vault git matrix music; do
    d="$prefix.$BASE_DOMAIN"
    code=$(curl -s -k -o /dev/null -w "%{http_code}" --resolve "$d:443:$HOST_IP" "https://$d/")
    echo "$d -> HTTP $code"
done
for prefix in "torrent.$BASE_DOMAIN/transmission/web/" "ytdl.$BASE_DOMAIN" "proxy.$BASE_DOMAIN/ui/" "lxc.$BASE_DOMAIN"; do
    h=$(echo "$prefix" | cut -d/ -f1)
    code=$(curl -s -k -o /dev/null -w "%{http_code}" -u "$AUTH_USER:$AUTH_PASS" --resolve "$h:443:$HOST_IP" "https://$prefix")
    echo "$prefix (Auth) -> HTTP $code"
done
if [ "${ENABLE_ADGUARD_HOME:-no}" = "yes" ]; then
    code=$(curl -s -k -o /dev/null -w "%{http_code}" -u "$AUTH_USER:$AUTH_PASS" --resolve "adguard.$BASE_DOMAIN:443:$HOST_IP" "https://adguard.$BASE_DOMAIN")
    echo "adguard.$BASE_DOMAIN (Auth) -> HTTP $code"
fi
