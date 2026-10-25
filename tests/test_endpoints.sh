#!/bin/sh
# Zero-Docker Alpine Homelab - Rapid Endpoint Health Check
AUTH_USER="${AUTH_USER:-admin}"
AUTH_PASS="${AUTH_PASS:-admin}"
BASE_DOMAIN="${BASE_DOMAIN:-lan}"
HOST_IP="${HOST_IP:-127.0.0.1}"

echo "=== DNS RESOLUTION ==="
for prefix in vault git matrix music torrent ytdl proxy lxc; do
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
