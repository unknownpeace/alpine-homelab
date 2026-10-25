#!/usr/bin/env python3
# Test dynamic toggling and port arbitration between sing-box (53/5353) and AdGuard Home (53/3001)
import subprocess, time, sys

sys.stdout.reconfigure(encoding='utf-8')

VM_IP = "192.168.1.147"

def run_ssh(cmd, timeout=180):
    res = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"root@{VM_IP}", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )
    return res.returncode, res.stdout, res.stderr

print("[*] 1. Enabling AdGuard Home in /opt/homelab/config.env...", flush=True)
code, out, err = run_ssh("sed -i 's/ENABLE_ADGUARD_HOME=.*/ENABLE_ADGUARD_HOME=\"yes\"/' /opt/homelab/config.env")
assert code == 0, f"Failed to edit config.env: {err}"

print("[*] 2. Running /usr/local/bin/homelab install...", flush=True)
code, out, err = run_ssh("/usr/local/bin/homelab install", timeout=300)
assert code == 0, f"Installer failed: {err}\n{out}"

print("[*] 3. Verifying port listening layout...", flush=True)
code, out, err = run_ssh("netstat -tulpn | grep -E ':53 |:5353 |:3001 '")
print(out, flush=True)
assert ":53 " in out and ("adguard" in out.lower()), f"AdGuard Home should be listening on port 53. Output:\n{out}"
assert ":5353 " in out and "sing-box" in out, f"sing-box should be listening on port 5353. Output:\n{out}"
assert ":3001 " in out and ("adguard" in out.lower()), f"AdGuard Home UI should be listening on port 3001. Output:\n{out}"

print("[*] 4. Testing DNS resolution through AdGuard Home (port 53 -> upstream 5353 sing-box)...", flush=True)
code, out, err = run_ssh("nslookup ya.ru 127.0.0.1")
assert code == 0 and ("77.88." in out or "2a02:" in out or "5.255." in out), f"External DNS resolution failed: {out}"
code, out, err = run_ssh("nslookup vault.lan 127.0.0.1")
assert code == 0 and "192.168.1.147" in out, f"Local *.lan DNS resolution failed: {out}"

print("[*] 5. Testing AdGuard Home HTTPS UI via Caddy reverse proxy...", flush=True)
code, out, err = run_ssh("curl -s -k -o /dev/null -w '%{http_code}' -u admin:admin --resolve adguard.lan:443:127.0.0.1 https://adguard.lan/")
print(f"AdGuard UI HTTP status: {out}", flush=True)
assert out in ["200", "302"], f"AdGuard UI returned HTTP {out}"

print("[*] 6. Running homelab doctor with AdGuard Home enabled...", flush=True)
code, out, err = run_ssh("/usr/local/bin/homelab doctor")
print(out, flush=True)
assert "Doctor Result: All systems operational! (0 issues found)" in out

print("[*] 7. Disabling AdGuard Home in /opt/homelab/config.env...", flush=True)
code, out, err = run_ssh("sed -i 's/ENABLE_ADGUARD_HOME=.*/ENABLE_ADGUARD_HOME=\"no\"/' /opt/homelab/config.env")
assert code == 0

print("[*] 8. Re-running /usr/local/bin/homelab install to revert port 53 to sing-box...", flush=True)
code, out, err = run_ssh("/usr/local/bin/homelab install", timeout=300)
assert code == 0, f"Installer failed on revert: {err}\n{out}"

print("[*] 9. Verifying port 53 listening layout after revert...", flush=True)
code, out, err = run_ssh("netstat -tulpn | grep -E ':53 |:5353 |:3001 ' || true")
print(out, flush=True)
assert ":53 " in out and "sing-box" in out, f"sing-box should be back on port 53. Output:\n{out}"
assert ":5353 " not in out, f"sing-box port 5353 should be gone. Output:\n{out}"
assert "adguard" not in out.lower(), f"AdGuard Home should be stopped. Output:\n{out}"

print("[*] 10. Running final homelab doctor after revert...", flush=True)
code, out, err = run_ssh("/usr/local/bin/homelab doctor")
assert "Doctor Result: All systems operational! (0 issues found)" in out

print("\n[SUCCESS] Dynamic AdGuard Home port 53 arbitration test PASSED!", flush=True)
