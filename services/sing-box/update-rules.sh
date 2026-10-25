#!/bin/sh
set -e
# sing-box: Download high-performance GeoIP and GeoSite binary rule-sets (.srs)

RULE_BASE="https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box"
RULE_BACKUP="https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/sing-box"
mkdir -p /var/lib/sing-box/rules

download_srs() {
    local cat="$1"
    local file="$2"
    local dest="/var/lib/sing-box/rules/$file"
    if ! curl -fL --retry 3 --connect-timeout 6 "$RULE_BASE/$cat/$file" -o "$dest" 2>/dev/null; then
        curl -fL --retry 3 --connect-timeout 6 "$RULE_BACKUP/$cat/$file" -o "$dest" 2>/dev/null || true
    fi
}

echo "Updating GeoIP and GeoSite rules..."
download_srs "rule-set-geoip" "geoip-ru.srs"
download_srs "rule-set-geosite" "geosite-tld-ru.srs"
download_srs "rule-set-geosite" "geosite-category-ru.srs"
download_srs "rule-set-geosite" "geosite-ru-blocked.srs"

AI_RULE_BASE="https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@sing/geo/geosite/category-ai-!cn.srs"
AI_RULE_BACKUP="https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/category-ai-!cn.srs"
if ! curl -fL --retry 3 --connect-timeout 6 "$AI_RULE_BASE" -o /var/lib/sing-box/rules/geosite-category-ai.srs 2>/dev/null; then
    curl -fL --retry 3 --connect-timeout 6 "$AI_RULE_BACKUP" -o /var/lib/sing-box/rules/geosite-category-ai.srs 2>/dev/null || true
fi

echo "Downloading and compiling AdGuard DNS Filter to .srs..."
ADGUARD_FILTER_URL="https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt"
ADGUARD_FILTER_BACKUP="https://raw.githubusercontent.com/AdguardTeam/AdguardSDNSFilter/gh-pages/Filters/filter.txt"
if ! curl -fL --retry 3 --connect-timeout 8 "$ADGUARD_FILTER_URL" -o /tmp/adguard_filter.txt 2>/dev/null; then
    curl -fL --retry 3 --connect-timeout 8 "$ADGUARD_FILTER_BACKUP" -o /tmp/adguard_filter.txt 2>/dev/null || true
fi
if [ -f /tmp/adguard_filter.txt ]; then
    sing-box rule-set convert /tmp/adguard_filter.txt -t adguard -o /var/lib/sing-box/rules/adguard-dns.srs
    rm -f /tmp/adguard_filter.txt
fi

echo "Rule-sets successfully updated."
