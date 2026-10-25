#!/usr/bin/env python3
# Zero-Docker Alpine Homelab Appliance (Bare-Metal) - Automated Regression Test Suite
import subprocess, time, sys, os
sys.stdout.reconfigure(encoding='utf-8')

VBOX = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM_NAME = "alpine"
SNAPSHOT_NAME = "Чистая система с выполненым обновлением пакетов и все"
VM_IP = "192.168.1.147"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFY_LXC_PATH = os.path.join(REPO_DIR, "tests", "verify_lxc_api.py")

def log(msg): print(f"[*] {msg}", flush=True)

def step_poweroff_and_restore():
    log(f"Powering off VM '{VM_NAME}'...")
    subprocess.run([VBOX, "controlvm", VM_NAME, "poweroff"], capture_output=True)
    time.sleep(3)
    log(f"Restoring clean baseline snapshot: '{SNAPSHOT_NAME}'...")
    res = subprocess.run([VBOX, "snapshot", VM_NAME, "restore", SNAPSHOT_NAME], capture_output=True, text=True)
    if res.returncode != 0:
        log(f"Snapshot restore failed: {res.stderr}")
        return False
    # Ensure promiscuous mode is enabled on bridged NIC for LXC containers to obtain router DHCP
    subprocess.run([VBOX, "modifyvm", VM_NAME, "--nicpromisc1", "allow-all"], capture_output=True)
    log("Booting VM...")
    res = subprocess.run([VBOX, "startvm", VM_NAME, "--type", "gui"], capture_output=True, text=True)
    if res.returncode != 0:
        subprocess.run([VBOX, "startvm", VM_NAME, "--type", "headless"], capture_output=True, text=True)
    return True

def step_wait_ssh():
    log("Waiting for host SSH readiness on port 22...")
    for i in range(35):
        res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=2", f"root@{VM_IP}", "echo READY"], capture_output=True, text=True)
        if res.returncode == 0 and "READY" in res.stdout:
            log(f"Host SSH is online (attempt {i+1}).")
            return True
        time.sleep(2)
    return False

def step_upload_script():
    log("Uploading alpine-homelab modular repository to VM at /opt/homelab...")
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"root@{VM_IP}", "rm -rf /opt/homelab && mkdir -p /opt/homelab /root"], check=True)
    for item in ["core", "lib", "services", "tests", "homelab.sh", "bootstrap.sh", "install-homelab-baremetal.sh", "config.env.example"]:
        src = os.path.join(REPO_DIR, item)
        subprocess.run(["scp", "-r", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", src, f"root@{VM_IP}:/opt/homelab/"], check=True)
    subprocess.run(["scp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", VERIFY_LXC_PATH, f"root@{VM_IP}:/root/verify_lxc_api.py"], check=True)
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"root@{VM_IP}", "ln -sf /opt/homelab/homelab.sh /usr/local/bin/homelab && chmod -R +x /opt/homelab/*.sh /opt/homelab/core/*.sh /opt/homelab/services/*/*.sh /root/verify_lxc_api.py"], check=True)
    return True

def step_run_installer():
    log("Executing unattended deployment: /usr/local/bin/homelab install...")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"root@{VM_IP}", "/usr/local/bin/homelab", "install"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
    for line in iter(proc.stdout.readline, ''):
        print(line, end='', flush=True)
    proc.wait()
    return proc.returncode == 0

def step_verify_all():
    log("Executing comprehensive post-install test suite on Bare-Metal host...")
    verify_script = '''
    echo "=== OPENRC SERVICES STATUS ==="
    rc-status default

    echo ""
    echo "=== TCP BBR & CONGESTION CONTROL ==="
    sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc

    echo ""
    echo "=== SING-BOX DNS RESOLUTION (*.lan, Direct & Ad-blocking) ==="
    nslookup vault.lan 127.0.0.1
    nslookup lxc.lan 127.0.0.1
    nslookup ya.ru 127.0.0.1
    nslookup doubleclick.net 127.0.0.1 || true

    echo ""
    echo "=== SING-BOX 1.14 CONFIG & RULE-SET VERIFICATION ==="
    sing-box check -c /etc/sing-box/config.json
    ls -lh /var/lib/sing-box/rules/
    echo -n "Match doubleclick.net (adguard-dns): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/adguard-dns.srs doubleclick.net 2>&1
    echo -n "Match youtube.com (blocked): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geosite-ru-blocked.srs youtube.com 2>&1
    echo -n "Match ya.ru (tld-ru): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geosite-tld-ru.srs ya.ru 2>&1
    echo -n "Match vk.com (category-ru): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geosite-category-ru.srs vk.com 2>&1
    echo -n "Match 77.88.8.8 (geoip-ru): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geoip-ru.srs 77.88.8.8 2>&1
    echo -n "Match chatgpt.com (category-ai): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geosite-category-ai.srs chatgpt.com 2>&1
    echo -n "Match claude.ai (category-ai): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geosite-category-ai.srs claude.ai 2>&1
    echo -n "Match gemini.google.com (category-ai): "
    sing-box rule-set match -f binary /var/lib/sing-box/rules/geosite-category-ai.srs gemini.google.com 2>&1

    sleep 3
    echo ""
    echo "=== HTTPS ENDPOINTS (PUBLIC & REDIRECTS) ==="
    for d in vault.lan git.lan matrix.lan music.lan; do
        for attempt in 1 2 3 4 5; do
            code=$(curl -s -k -o /dev/null -w "%{http_code}" --resolve $d:443:127.0.0.1 https://$d/)
            if [ "$code" = "200" ] || [ "$code" = "302" ]; then break; fi
            sleep 1
        done
        echo "$d -> HTTP $code"
    done

    echo ""
    echo "=== HTTPS ENDPOINTS (BASIC AUTH + SMART REDIRECTS) ==="
    for d in "proxy.lan/" "torrent.lan/" "ytdl.lan/" "lxc.lan/"; do
        h=$(echo $d | cut -d/ -f1)
        code=$(curl -s -k -o /dev/null -w "%{http_code}" -u admin:admin --resolve $h:443:127.0.0.1 "https://$d")
        echo "https://$d -> HTTP $code"
    done

    echo ""
    echo "=== HOMELAB DOCTOR DIAGNOSTICS ==="
    /usr/local/bin/homelab doctor

    echo ""
    echo "=== LXC MANAGER API & SANDBOX VERIFICATION ==="
    python3 /root/verify_lxc_api.py

    echo ""
    echo "=== MEMORY AUDIT ==="
    free -h
    '''
    res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", f"root@{VM_IP}", verify_script], capture_output=True, text=True)
    print(res.stdout)
    return ("vault.lan -> HTTP 200" in res.stdout and 
            "git.lan -> HTTP 200" in res.stdout and 
            "lxc.lan/ -> HTTP 200" in res.stdout and 
            "Doctor Result: All systems operational!" in res.stdout and
            "ALL LXC MANAGER TESTS PASSED!" in res.stdout)

def main():
    if not step_poweroff_and_restore(): sys.exit(1)
    if not step_wait_ssh(): sys.exit(1)
    if not step_upload_script(): sys.exit(1)
    if not step_run_installer(): sys.exit(1)
    if not step_verify_all(): sys.exit(1)
    log("SUCCESS: All modular Bare-Metal OpenRC services passed verification!")

if __name__ == "__main__": main()
