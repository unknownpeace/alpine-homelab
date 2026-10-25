#!/usr/bin/env python3
import json, urllib.request, urllib.parse, base64, subprocess, sys, time, os, socket, shutil

def get_default_ip():
    env_ip = os.environ.get("IP_HOST")
    if env_ip and env_ip != "127.0.0.1":
        return env_ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Load homelab config.env if available to source variables seamlessly
def load_env_file():
    for p in ["/opt/homelab/config.env", os.path.join(os.path.dirname(__file__), "../../config.env")]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k not in os.environ and v:
                            os.environ[k] = v
            except Exception:
                pass
            break

load_env_file()

RULE_DIR = "/var/lib/sing-box/rules"
os.makedirs(RULE_DIR, exist_ok=True)
rules_map = {
    "geoip-ru.srs": "https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box/rule-set-geoip/geoip-ru.srs",
    "geosite-tld-ru.srs": "https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box/rule-set-geosite/geosite-tld-ru.srs",
    "geosite-category-ru.srs": "https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box/rule-set-geosite/geosite-category-ru.srs",
    "geosite-ru-blocked.srs": "https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box/rule-set-geosite/geosite-ru-blocked.srs",
    "geosite-category-ai.srs": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@sing/geo/geosite/category-ai-!cn.srs",
}
backup_rules_map = {
    "geoip-ru.srs": "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/sing-box/rule-set-geoip/geoip-ru.srs",
    "geosite-tld-ru.srs": "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/sing-box/rule-set-geosite/geosite-tld-ru.srs",
    "geosite-category-ru.srs": "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/sing-box/rule-set-geosite/geosite-category-ru.srs",
    "geosite-ru-blocked.srs": "https://raw.githubusercontent.com/runetfreedom/russia-v2ray-rules-dat/release/sing-box/rule-set-geosite/geosite-ru-blocked.srs",
    "geosite-category-ai.srs": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/sing/geo/geosite/category-ai-!cn.srs",
}
for rfile, main_url in rules_map.items():
    rpath = os.path.join(RULE_DIR, rfile)
    if not os.path.exists(rpath) or os.path.getsize(rpath) == 0:
        for url in [main_url, backup_rules_map[rfile]]:
            try:
                urllib.request.urlretrieve(url, rpath)
                if os.path.exists(rpath) and os.path.getsize(rpath) > 0:
                    break
            except Exception:
                pass

ADGUARD_ENABLED = os.environ.get("ENABLE_ADGUARD_HOME", "no").lower() == "yes"

adguard_srs = os.path.join(RULE_DIR, "adguard-dns.srs")
if not ADGUARD_ENABLED:
    if not os.path.exists(adguard_srs) or os.path.getsize(adguard_srs) == 0:
        flist = "/tmp/adguard_filter.txt"
        for url in ["https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt", "https://raw.githubusercontent.com/AdguardTeam/AdguardSDNSFilter/gh-pages/Filters/filter.txt"]:
            try:
                urllib.request.urlretrieve(url, flist)
                if os.path.exists(flist) and os.path.getsize(flist) > 0:
                    subprocess.run(["sing-box", "rule-set", "convert", flist, "-t", "adguard", "-o", adguard_srs], check=False)
                    if os.path.exists(flist): os.remove(flist)
                    if os.path.exists(adguard_srs) and os.path.getsize(adguard_srs) > 0:
                        break
            except Exception:
                pass

# Determine target URL or node string
if len(sys.argv) > 1 and sys.argv[1].strip():
    SUB_URL = " ".join(sys.argv[1:]).strip()
else:
    SUB_URL = os.environ.get("PROXY_INPUT", os.environ.get("SUB_URL", "direct")).strip()

def parse_single_node(link: str) -> dict:
    link = link.strip()
    if not link: return None
    try:
        p = urllib.parse.urlparse(link)
    except Exception:
        return None
    scheme = p.scheme.lower()
    tag = urllib.parse.unquote(p.fragment) if p.fragment else f"{scheme.upper()}-{p.hostname or 'node'}"

    # 1. VLESS
    if scheme == "vless":
        uuid = p.username
        server = p.hostname
        port = p.port or 443
        qs = urllib.parse.parse_qs(p.query)
        params = {k: v[0] for k, v in qs.items()}
        ob = {
            "type": "vless",
            "tag": tag,
            "server": server,
            "server_port": port,
            "uuid": uuid
        }
        if "flow" in params:
            ob["flow"] = params["flow"]
        t_type = params.get("type", "tcp")
        if t_type == "ws":
            ws_cfg = {"type": "ws"}
            if "path" in params: ws_cfg["path"] = params["path"]
            if "host" in params: ws_cfg["headers"] = {"Host": params["host"]}
            ob["transport"] = ws_cfg
        elif t_type == "grpc":
            grpc_cfg = {"type": "grpc"}
            if "serviceName" in params: grpc_cfg["service_name"] = params["serviceName"]
            ob["transport"] = grpc_cfg
        elif t_type in ["xhttp", "splithttp"]:
            xhttp_cfg = {"type": "xhttp"}
            if "path" in params: xhttp_cfg["path"] = params["path"]
            if "host" in params: xhttp_cfg["host"] = params["host"]
            if "mode" in params: xhttp_cfg["mode"] = params["mode"]
            xhttp_cfg["x_padding_bytes"] = params.get("x_padding_bytes", params.get("padding", "100-1000"))
            ob["transport"] = xhttp_cfg
        elif t_type == "http":
            http_cfg = {"type": "http"}
            if "path" in params: http_cfg["path"] = params["path"]
            if "host" in params: http_cfg["host"] = [params["host"]]
            ob["transport"] = http_cfg
        sec = params.get("security")
        if sec in ["tls", "reality"]:
            tls_cfg = {"enabled": True}
            sni = params.get("sni") or params.get("peer")
            if sni: tls_cfg["server_name"] = sni
            if "alpn" in params: tls_cfg["alpn"] = params["alpn"].split(",")
            if params.get("insecure") in ["1", "true"]: tls_cfg["insecure"] = True
            fp = params.get("fp")
            if fp: tls_cfg["utls"] = {"enabled": True, "fingerprint": fp}
            if sec == "reality":
                real_cfg = {"enabled": True}
                if "pbk" in params: real_cfg["public_key"] = params["pbk"]
                if "sid" in params: real_cfg["short_id"] = params["sid"]
                tls_cfg["reality"] = real_cfg
            ob["tls"] = tls_cfg
        return ob

    # 2. Shadowsocks
    elif scheme == "ss":
        server, port, method, password = None, None, None, None
        if "@" in p.netloc:
            userinfo, hostport = p.netloc.split("@", 1)
            try:
                padded = userinfo + "=" * (-len(userinfo) % 4)
                decoded = base64.b64decode(padded).decode("utf-8")
                if ":" in decoded: method, password = decoded.split(":", 1)
            except Exception:
                if ":" in userinfo: method, password = userinfo.split(":", 1)
            if ":" in hostport:
                server, port_str = hostport.split(":", 1)
                port = int(port_str.split("/")[0])
        else:
            try:
                padded = p.netloc + "=" * (-len(p.netloc) % 4)
                decoded = base64.b64decode(padded).decode("utf-8")
                if "@" in decoded:
                    userinfo, hostport = decoded.split("@", 1)
                    if ":" in userinfo: method, password = userinfo.split(":", 1)
                    if ":" in hostport:
                        server, port_str = hostport.split(":", 1)
                        port = int(port_str.split("/")[0])
            except Exception:
                pass
        if server and port and method and password:
            return {
                "type": "shadowsocks",
                "tag": tag,
                "server": server,
                "server_port": port,
                "method": method,
                "password": password
            }

    # 3. Trojan
    elif scheme == "trojan":
        password = p.username or p.netloc.split("@")[0]
        server = p.hostname
        port = p.port or 443
        qs = urllib.parse.parse_qs(p.query)
        params = {k: v[0] for k, v in qs.items()}
        ob = {
            "type": "trojan",
            "tag": tag,
            "server": server,
            "server_port": port,
            "password": password,
            "tls": {
                "enabled": True,
                "server_name": params.get("sni") or server,
                "insecure": params.get("allowInsecure") in ["1", "true"]
            }
        }
        if "alpn" in params:
            ob["tls"]["alpn"] = params["alpn"].split(",")
        return ob

    # 4. Hysteria 2
    elif scheme in ["hysteria2", "hy2"]:
        auth = p.username or (p.netloc.split("@")[0] if "@" in p.netloc else "")
        server = p.hostname
        port = p.port or 443
        qs = urllib.parse.parse_qs(p.query)
        params = {k: v[0] for k, v in qs.items()}
        ob = {
            "type": "hysteria2",
            "tag": tag,
            "server": server,
            "server_port": port,
            "password": auth,
            "tls": {
                "enabled": True,
                "server_name": params.get("sni") or server,
                "insecure": params.get("insecure") in ["1", "true"]
            }
        }
        if "obfs" in params:
            ob["obfs"] = {
                "type": params["obfs"],
                "password": params.get("obfs-password", "")
            }
        return ob

    return None

TARGET = SUB_URL.strip()
raw_outbounds = []

if TARGET and TARGET.lower() != "direct":
    if any(TARGET.startswith(proto) for proto in ["vless://", "ss://", "trojan://", "hy2://", "hysteria2://"]):
        node = parse_single_node(TARGET)
        if node:
            raw_outbounds.append(node)
    elif TARGET.startswith("http://") or TARGET.startswith("https://"):
        urls_to_try = [TARGET]
        if "fmt=best" in TARGET or "fmt=v2ray" in TARGET or "fmt=clash" in TARGET:
            urls_to_try.insert(0, TARGET.replace("fmt=best", "fmt=sing-box").replace("fmt=v2ray", "fmt=sing-box").replace("fmt=clash", "fmt=sing-box"))
        elif "fmt=" not in TARGET and any(x in TARGET for x in ["/sub/", "subscription", "token", "link"]):
            sep = "&" if "?" in TARGET else "?"
            urls_to_try.insert(0, TARGET + sep + "fmt=sing-box")

        for turl in urls_to_try:
            req = urllib.request.Request(turl, headers={"User-Agent": "sing-box/1.14.3"})
            resp_data = None
            for attempt in range(1, 3):
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        resp_data = resp.read().decode('utf-8', errors='ignore')
                        break
                except Exception as e:
                    time.sleep(1)
            if not resp_data:
                continue

            # 1. Try JSON dict or list
            try:
                j = json.loads(resp_data)
                if isinstance(j, dict) and "outbounds" in j:
                    raw_outbounds = j.get("outbounds", [])
                    if raw_outbounds:
                        break
                elif isinstance(j, list):
                    if j and isinstance(j[0], dict) and ("type" in j[0] or "server" in j[0]):
                        raw_outbounds = j
                        break
                    v2ray_nodes = []
                    for item in j:
                        if isinstance(item, dict):
                            remarks = item.get("remarks") or "Node"
                            for ob in item.get("outbounds", []):
                                if ob.get("type") in ["vless", "vmess", "trojan", "shadowsocks", "hysteria2"]:
                                    ob["tag"] = remarks
                                    v2ray_nodes.append(ob)
                    if v2ray_nodes:
                        raw_outbounds = v2ray_nodes
                        break
            except Exception:
                pass

            # 2. Try Base64 or plain text lines
            text_lines = []
            try:
                padded = resp_data.strip() + "=" * (-len(resp_data.strip()) % 4)
                decoded_text = base64.b64decode(padded).decode('utf-8', errors='ignore')
                text_lines = decoded_text.splitlines()
            except Exception:
                text_lines = resp_data.splitlines()
            for line in text_lines:
                line = line.strip()
                if any(line.startswith(proto) for proto in ["vless://", "ss://", "trojan://", "hy2://", "hysteria2://"]):
                    n = parse_single_node(line)
                    if n:
                        raw_outbounds.append(n)
            if raw_outbounds:
                break

def is_dummy(ob):
    tag = ob.get("tag", "")
    server = ob.get("server", "")
    port = ob.get("server_port", 0)
    if server in ["127.0.0.1", "0.0.0.0"] or port == 1:
        return True
    if any(tag.startswith(p) for p in ["ℹ", "⛔", "📰", "⚠️"]):
        return True
    return False

clean_nodes = []
provider_groups = []
seen_tags = set()

for ob in raw_outbounds:
    ob.pop("idle_only", None)
    tag = ob.get("tag")
    t = ob.get("type")
    if not tag or tag in seen_tags or is_dummy(ob):
        continue
    seen_tags.add(tag)
    if t in ["selector", "urltest"]:
        if tag in ["select", "🌐 Автовыбор", "🚀 Выбор узла", "🤖 Нейросети", "Torrent"]:
            continue
        provider_groups.append(ob)
    elif t not in ["direct", "block", "dns-out"]:
        clean_nodes.append(ob)

node_tags = [ob["tag"] for ob in clean_nodes]

custom_groups = [
    {
        "type": "urltest",
        "tag": "🌐 Автовыбор",
        "outbounds": node_tags if node_tags else ["direct"],
        "url": "https://www.gstatic.com/generate_204",
        "interval": "3m",
        "tolerance": 50
    },
    {
        "type": "selector",
        "tag": "🚀 Выбор узла",
        "outbounds": ["🌐 Автовыбор", "direct"] + node_tags,
        "default": "🌐 Автовыбор"
    },
    {
        "type": "selector",
        "tag": "🤖 Нейросети",
        "outbounds": ["🌐 Автовыбор", "🚀 Выбор узла", "direct"] + node_tags,
        "default": "🌐 Автовыбор"
    },
    {
        "type": "selector",
        "tag": "Torrent",
        "outbounds": ["direct"] + node_tags,
        "default": "direct"
    }
]

main_selector = "🚀 Выбор узла"
final_outbounds = custom_groups + provider_groups + clean_nodes + [{"type": "direct", "tag": "direct"}]
IP_HOST = get_default_ip()
raw_base_domain = os.environ.get("BASE_DOMAIN", "lan").lstrip(".")
try:
    raw_base_domain = raw_base_domain.encode("ascii", "ignore").decode("ascii")
except Exception:
    pass
import re
BASE_DOMAIN = re.sub(r'[^a-zA-Z0-9.-]', '', raw_base_domain).lower().strip('.') or "lan"
DNS_PRIMARY = os.environ.get("DNS_UPSTREAM_PRIMARY", "https://dns.adguard-dns.com/dns-query")
DNS_SECONDARY = os.environ.get("DNS_UPSTREAM_SECONDARY", "1.1.1.1")
if ADGUARD_ENABLED:
    dns_in_listen = "127.0.0.1"
    dns_in_port = int(os.environ.get("SINGBOX_DNS_PORT", "5353"))
    tun_dns_mode = "disabled"
else:
    dns_in_listen = "0.0.0.0"
    dns_in_port = int(os.environ.get("SINGBOX_DNS_PORT", "53"))
    tun_dns_mode = "hijack"

predefined_hosts = {
    f"vault.{BASE_DOMAIN}": IP_HOST,
    f"git.{BASE_DOMAIN}": IP_HOST,
    f"matrix.{BASE_DOMAIN}": IP_HOST,
    f"music.{BASE_DOMAIN}": IP_HOST,
    f"torrent.{BASE_DOMAIN}": IP_HOST,
    f"ytdl.{BASE_DOMAIN}": IP_HOST,
    f"proxy.{BASE_DOMAIN}": IP_HOST,
    f"lxc.{BASE_DOMAIN}": IP_HOST,
    f"adguard.{BASE_DOMAIN}": IP_HOST,
    f"monitor.{BASE_DOMAIN}": IP_HOST,
    f"id.{BASE_DOMAIN}": IP_HOST,
    f"auth.{BASE_DOMAIN}": IP_HOST,
    f"homelab.{BASE_DOMAIN}": IP_HOST,
    "homelab": IP_HOST,
    "homelab.lan": IP_HOST
}

dns_rules = [
    {"domain": ["homelab", "homelab.lan"], "server": "hosts-lan"},
    {"domain_suffix": [f".{BASE_DOMAIN}"], "server": "hosts-lan"}
]
if not ADGUARD_ENABLED:
    dns_rules.append({"rule_set": "adguard-dns", "action": "reject"})
dns_rules.extend([
    {"rule_set": "geosite-category-ai", "server": "ai-dns"},
    {"rule_set": "geosite-ru-blocked", "server": "remote-dns"},
    {"rule_set": ["geosite-tld-ru", "geosite-category-ru"], "server": "direct-dns"}
])

config = {
    "log": {
        "level": "info",
        "output": "/var/log/sing-box.log",
        "timestamp": True
    },
    "dns": {
        "servers": [
            {
                "tag": "hosts-lan",
                "type": "hosts",
                "predefined": predefined_hosts
            },
            {"tag": "ai-dns", "type": "tls", "server": DNS_SECONDARY, "detour": "🤖 Нейросети"},
            {"tag": "remote-dns", "type": "tls", "server": DNS_SECONDARY, "detour": main_selector},
            {"tag": "direct-dns", "type": "udp", "server": "77.88.8.8", "detour": "direct"}
        ],
        "rules": dns_rules,
        "final": "remote-dns",
        "strategy": "prefer_ipv4",
        "independent_cache": True
    },
    "inbounds": [
        {
            "type": "tun",
            "tag": "tun-in",
            "interface_name": "tun0",
            "address": ["172.19.0.1/30"],
            "auto_route": True,
            "auto_redirect": True,
            "strict_route": False,
            "stack": "mixed"
        },
        {
            "type": "direct",
            "tag": "dns-in",
            "listen": dns_in_listen,
            "listen_port": dns_in_port
        }
    ],
    "outbounds": final_outbounds,
    "route": {
        "default_domain_resolver": "direct-dns",
        "rule_set": (
            ([{"tag": "adguard-dns", "type": "local", "format": "binary", "path": "/var/lib/sing-box/rules/adguard-dns.srs"}] if not ADGUARD_ENABLED else []) +
            [
                {
                    "tag": "geosite-category-ai",
                    "type": "local",
                    "format": "binary",
                    "path": "/var/lib/sing-box/rules/geosite-category-ai.srs"
                },
                {
                    "tag": "geosite-ru-blocked",
                    "type": "local",
                    "format": "binary",
                    "path": "/var/lib/sing-box/rules/geosite-ru-blocked.srs"
                },
                {
                    "tag": "geosite-tld-ru",
                    "type": "local",
                    "format": "binary",
                    "path": "/var/lib/sing-box/rules/geosite-tld-ru.srs"
                },
                {
                    "tag": "geosite-category-ru",
                    "type": "local",
                    "format": "binary",
                    "path": "/var/lib/sing-box/rules/geosite-category-ru.srs"
                },
                {
                    "tag": "geoip-ru",
                    "type": "local",
                    "format": "binary",
                    "path": "/var/lib/sing-box/rules/geoip-ru.srs"
                }
            ]
        ),
        "rules": (
            [
                {"action": "sniff"},
                {"inbound": "dns-in", "action": "hijack-dns"},
                ({"protocol": "dns", "port": 53, "action": "hijack-dns"} if not ADGUARD_ENABLED else {"inbound": "dns-in", "action": "hijack-dns"}),
                {"protocol": "bittorrent", "outbound": "Torrent"},
                {"ip_is_private": True, "outbound": "direct"},
                {"ip_cidr": ["100.64.0.0/10"], "outbound": "direct"}
            ] +
            ([{"rule_set": "adguard-dns", "action": "reject", "method": "drop"}] if not ADGUARD_ENABLED else []) +
            [
                {"rule_set": "geosite-category-ai", "outbound": "🤖 Нейросети"},
                {"rule_set": "geosite-ru-blocked", "outbound": main_selector},
                {"rule_set": ["geosite-tld-ru", "geosite-category-ru"], "outbound": "direct"},
                {"rule_set": "geoip-ru", "outbound": "direct"}
            ]
        ),
        "final": main_selector,
        "auto_detect_interface": True
    },
    "experimental": {
        "clash_api": {
            "external_controller": "127.0.0.1:9090",
            "external_ui": "/var/lib/sing-box/ui"
        }
    }
}

def sanitize_obj(obj):
    if isinstance(obj, str):
        try:
            return obj.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
        except Exception:
            return obj.encode('utf-8', 'replace').decode('utf-8')
    elif isinstance(obj, dict):
        return {sanitize_obj(k): sanitize_obj(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_obj(x) for x in obj]
    return obj

def persist_proxy_input(val):
    for env_path in ["/opt/homelab/config.env"]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                found = False
                new_lines = []
                for line in lines:
                    if line.strip().startswith("PROXY_INPUT="):
                        new_lines.append(f'PROXY_INPUT="{val}"\n')
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f'PROXY_INPUT="{val}"\n')
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
            except Exception:
                pass

config = sanitize_obj(config)
cfg_dir = "/etc/sing-box"
cfg_path = os.path.join(cfg_dir, "config.json")
cfg_bak = os.path.join(cfg_dir, "config.json.bak")
os.makedirs(cfg_dir, exist_ok=True)

if os.path.exists(cfg_path):
    try:
        shutil.copy2(cfg_path, cfg_bak)
    except Exception:
        pass

with open(cfg_path, "w", encoding="utf-8", errors="replace") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

# Validate config with sing-box check
chk = subprocess.run(["sing-box", "check", "-C", cfg_dir], capture_output=True, text=True)
if chk.returncode != 0:
    print(f"Error: sing-box configuration check failed: {chk.stderr or chk.stdout}", file=sys.stderr)
    if os.path.exists(cfg_bak):
        shutil.copy2(cfg_bak, cfg_path)
    sys.exit(1)

persist_proxy_input(TARGET)
subprocess.run(["rc-service", "sing-box", "restart"], check=False)
print("sing-box configuration updated and reloaded.")
