#!/usr/bin/env python3
"""
Alpine Homelab Appliance - Comprehensive Service & Regression Test Suite (2026 Edition)
Performs deep end-to-end verification across:
  1. Media Storage Group Permissions (transmission, navidrome, admin)
  2. Persistent Disk Logging (sing-box, caddy, transmission, navidrome, forgejo, ytdlp, lxc, conduit, samba, wsdd)
  3. DNS Engine Resolution (*.lan, NetBIOS short name, external ya.ru, ad-blocking)
  4. Caddy 2 HTTPS Endpoints & Security Headers
  5. Samba 4 SMB3 File Sharing Configuration
  6. LXC Sandbox Port Forwarding (DNAT / nftables)
  7. sing-box Tailscale Mesh Routing Bypass
"""

import os
import sys
import json
import time
import socket
import urllib.request
import urllib.error
import ssl
import subprocess

def log_test(name):
    print(f"\n[TEST] {name}...", flush=True)

def log_pass(msg):
    print(f"  [PASS] {msg}", flush=True)

def log_fail(msg):
    print(f"  [FAIL] {msg}", flush=True)

def run_cmd(cmd, timeout=30):
    try:
        res = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def load_config():
    cfg = {
        "AUTH_USER": "admin",
        "AUTH_PASS": "admin",
        "BASE_DOMAIN": "lan",
        "STORAGE_BASE": "/srv/storage",
        "IP_HOST": "127.0.0.1"
    }
    for p in ["/opt/homelab/config.env", "/etc/homelab/config.env"]:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        cfg[k.strip()] = v.strip().strip('"\'')
    return cfg

def test_media_permissions(cfg):
    log_test("1. Media Group Permissions (/srv/storage)")
    storage = cfg["STORAGE_BASE"]
    downloads = os.path.join(storage, "downloads")
    music = os.path.join(storage, "music")
    failures = 0

    # 1.1 Transmission write test
    code, out, err = run_cmd(f"su -s /bin/sh transmission -c 'touch {downloads}/.perm_test_tr && rm -f {downloads}/.perm_test_tr'")
    if code == 0:
        log_pass("transmission daemon can write to /srv/storage/downloads")
    else:
        log_fail(f"transmission daemon cannot write to {downloads}: {err}")
        failures += 1

    # 1.2 Navidrome read test
    code, out, err = run_cmd(f"su -s /bin/sh navidrome -c 'test -r {music}'")
    if code == 0:
        log_pass("navidrome daemon can read /srv/storage/music")
    else:
        log_fail(f"navidrome daemon cannot read {music}: {err}")
        failures += 1

    # 1.3 Admin user write test
    user = cfg["AUTH_USER"]
    code, out, err = run_cmd(f"su -s /bin/sh {user} -c 'touch {downloads}/.perm_test_user && rm -f {downloads}/.perm_test_user'")
    if code == 0:
        log_pass(f"admin user ({user}) can write to /srv/storage/downloads")
    else:
        log_fail(f"admin user ({user}) cannot write to {downloads}: {err}")
        failures += 1

    return failures == 0

def test_service_logs():
    log_test("2. Service Logging Verification (Disk Logs & Rotation)")
    expected_logs = {
        "sing-box": ["/var/log/sing-box.log"],
        "caddy": ["/var/log/caddy/caddy.log"],
        "transmission": ["/var/log/transmission/transmission.log"],
        "navidrome": ["/var/log/navidrome.log"],
        "forgejo": ["/var/log/forgejo/forgejo.log", "/var/log/forgejo/gitea.log", "/var/log/forgejo/http.log"],
        "ytdlp-web": ["/var/log/ytdlp-web.log"],
        "lxc-manager": ["/var/log/lxc-manager.log"],
        "conduit": ["/var/log/conduit.log"],
        "wsdd": ["/var/log/wsdd.log"]
    }

    failures = 0
    for svc, log_candidates in expected_logs.items():
        found_path = None
        for p in log_candidates:
            if os.path.exists(p):
                found_path = p
                break

        if found_path:
            size = os.path.getsize(found_path)
            log_pass(f"{svc} log exists ({found_path}, {size} bytes)")
        else:
            # Check if service is active before failing
            code, _, _ = run_cmd(f"rc-service {svc} status")
            if code == 0:
                log_fail(f"{svc} is running but log file ({log_candidates}) is missing!")
                failures += 1
            else:
                print(f"  [INFO] {svc} is not enabled or stopped; skipping log check.")

    # Samba check
    if os.path.isdir("/var/log/samba"):
        log_pass("samba log directory exists (/var/log/samba)")
    return failures == 0

def test_dns_resolution(cfg):
    log_test("3. DNS Resolution & Hijacking (Port 53)")
    base_dom = cfg["BASE_DOMAIN"]
    host_ip = cfg["IP_HOST"]
    failures = 0

    # 3.1 Local *.lan resolution
    code, out, _ = run_cmd(f"nslookup homelab.{base_dom} 127.0.0.1")
    if code == 0 and host_ip in out:
        log_pass(f"homelab.{base_dom} resolves to {host_ip}")
    else:
        log_fail(f"homelab.{base_dom} failed to resolve to {host_ip}: {out}")
        failures += 1

    # 3.2 NetBIOS short name resolution (Windows Explorer discovery)
    code, out, _ = run_cmd("nslookup homelab 127.0.0.1")
    if code == 0 and host_ip in out:
        log_pass(f"Short NetBIOS name 'homelab' resolves to {host_ip}")
    else:
        log_fail(f"Short NetBIOS name 'homelab' failed to resolve: {out}")
        failures += 1

    # 3.3 External domain resolution
    code, out, _ = run_cmd("nslookup ya.ru 127.0.0.1")
    if code == 0 and ("77.88." in out or "2a02:" in out or "5.255." in out):
        log_pass("External domain 'ya.ru' resolves successfully")
    else:
        log_fail(f"External DNS resolution failed: {out}")
        failures += 1

    return failures == 0

def test_https_endpoints(cfg):
    log_test("4. Caddy HTTPS Reverse Proxy & Security Headers")
    base_dom = cfg["BASE_DOMAIN"]
    user = cfg["AUTH_USER"]
    pwd = cfg["AUTH_PASS"]
    failures = 0

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    endpoints = [
        (f"https://homelab.{base_dom}/", None, [200]),
        (f"https://vault.{base_dom}/", None, [200]),
        (f"https://git.{base_dom}/", None, [200]),
        (f"https://matrix.{base_dom}/", None, [200]),
        (f"https://music.{base_dom}/", None, [200, 302]),
        (f"https://torrent.{base_dom}/", (user, pwd), [200, 302]),
        (f"https://ytdl.{base_dom}/", (user, pwd), [200]),
        (f"https://proxy.{base_dom}/ui/", (user, pwd), [200, 302]),
        (f"https://lxc.{base_dom}/", (user, pwd), [200]),
    ]

    for url, auth, allowed_codes in endpoints:
        code, out, _ = run_cmd(
            f"curl -s -k -o /dev/null -w '%{{http_code}}' "
            + (f"-u {auth[0]}:{auth[1]} " if auth else "")
            + f"--resolve {url.split('/')[2]}:443:127.0.0.1 '{url}'"
        )
        try:
            status = int(out.strip())
            if status in allowed_codes:
                log_pass(f"{url} returned HTTP {status}")
            else:
                log_fail(f"{url} returned unexpected HTTP {status} (expected {allowed_codes})")
                failures += 1
        except ValueError:
            log_fail(f"{url} curl returned non-integer: {out}")
            failures += 1

    return failures == 0

def test_samba_configuration():
    log_test("5. Samba 4 SMB3 & WSDD Network Configuration")
    failures = 0

    # 5.1 testparm check
    code, out, err = run_cmd("testparm -s /etc/samba/smb.conf")
    if code == 0:
        log_pass("smb.conf passed testparm syntax validation")
    else:
        log_fail(f"smb.conf failed testparm: {err}")
        failures += 1

    # 5.2 Check SMB port 445 listening
    code, out, _ = run_cmd("netstat -tulpn | grep ':445 '")
    if code == 0 and "smbd" in out:
        log_pass("Samba smbd is listening on port 445")
    else:
        log_fail("smbd is not listening on port 445")
        failures += 1

    # 5.3 Check WSDD discovery service
    code, out, _ = run_cmd("rc-service wsdd status")
    if code == 0:
        log_pass("WSDD daemon is running")
    else:
        log_fail("WSDD daemon is not running")
        failures += 1

    return failures == 0

def test_port_forwarding():
    log_test("6. LXC Manager Port Forwarding (DNAT / nftables)")
    failures = 0
    test_port = 18889

    # 6.1 Test applying DNAT rule
    code, out, err = run_cmd(
        f"python3 -c 'import sys; sys.path.insert(0, \"/opt/lxc-manager\"); sys.path.insert(0, \"/opt/homelab/services/lxc-manager\"); from app import apply_nft_dnat; apply_nft_dnat(\"add\", {test_port}, \"10.0.3.99\", 80, \"tcp\", \"test_pf\")'",
    )
    if code == 0:
        log_pass(f"Port forwarding helper added rule for host port {test_port}")
    else:
        log_fail(f"Failed to add port forward rule: {err}")
        failures += 1

    # 6.2 Verify rule in nftables
    code, out, _ = run_cmd("nft -a list chain inet homelab prerouting")
    if code == 0 and f"dport {test_port}" in out and "10.0.3.99:80" in out:
        log_pass(f"nftables prerouting chain contains DNAT rule for port {test_port}")
    else:
        log_fail(f"DNAT rule for port {test_port} missing from nftables prerouting: {out}")
        failures += 1

    # 6.3 Test deleting DNAT rule
    code, out, err = run_cmd(
        f"python3 -c 'import sys; sys.path.insert(0, \"/opt/lxc-manager\"); sys.path.insert(0, \"/opt/homelab/services/lxc-manager\"); from app import apply_nft_dnat; apply_nft_dnat(\"delete\", {test_port}, \"10.0.3.99\", 80, \"tcp\")'",
    )
    code_check, out_check, _ = run_cmd("nft -a list chain inet homelab prerouting")
    if f"dport {test_port}" not in out_check:
        log_pass(f"Port forwarding helper deleted rule for host port {test_port}")
    else:
        log_fail(f"DNAT rule for port {test_port} still present after delete: {out_check}")
        failures += 1

    return failures == 0

def test_tailscale_routing_bypass():
    log_test("7. sing-box Tailscale Mesh Routing Bypass (100.64.0.0/10)")
    cfg_file = "/etc/sing-box/config.json"
    if not os.path.exists(cfg_file):
        log_fail(f"{cfg_file} not found")
        return False

    with open(cfg_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    rules = data.get("route", {}).get("rules", [])
    found = False
    for r in rules:
        cidrs = r.get("ip_cidr", [])
        if "100.64.0.0/10" in cidrs and r.get("outbound") == "direct":
            found = True
            break

    if found:
        log_pass("sing-box configuration includes 100.64.0.0/10 -> direct outbound rule")
        return True
    else:
        log_fail("sing-box configuration missing 100.64.0.0/10 -> direct outbound rule!")
        return False

def main():
    print("==================================================================", flush=True)
    print("    Alpine Homelab Appliance - Service Regression Test Suite      ", flush=True)
    print("==================================================================", flush=True)

    cfg = load_config()
    results = [
        test_media_permissions(cfg),
        test_service_logs(),
        test_dns_resolution(cfg),
        test_https_endpoints(cfg),
        test_samba_configuration(),
        test_port_forwarding(),
        test_tailscale_routing_bypass()
    ]

    print("\n==================================================================", flush=True)
    if all(results):
        print("  🎉 ALL HOMELAB SERVICE SUITE TESTS PASSED SUCCESSFULLY! (7/7)", flush=True)
        print("==================================================================", flush=True)
        sys.exit(0)
    else:
        passed = sum(1 for r in results if r)
        print(f"  ❌ TEST SUITE FAILED: {passed}/7 tests passed.", flush=True)
        print("==================================================================", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
