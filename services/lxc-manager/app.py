#!/usr/bin/env python3
"""
LXC Sandbox Web Manager for Alpine Linux
Ultra-lightweight native WSGI application built with Bottle.
Manages isolated test/sandbox containers alongside bare-metal services.
Supports:
  - Official LinuxContainers catalog (Alpine, Debian, Ubuntu, Arch, Fedora, Rocky)
  - Home network LAN mode (br0 with DHCP from router) & NAT Sandbox (lxcbr0)
  - Snapshots and Rollback (lxc-snapshot)
  - Interactive Web Terminal (ttyd / xterm.js via WebSocket)
"""

import os
import sys
import re
import json
import time
import shutil
import subprocess
from bottle import Bottle, request, response, static_file, abort

app = Bottle()

LXC_DIR = "/var/lib/lxc"
CACHE_DIR = "/var/cache/lxc"
MINIROOTFS_CACHE = os.path.join(LXC_DIR, "cache", "alpine-minirootfs.tar.gz")

DISTRO_CATALOG = {
    "alpine": {
        "name": "Alpine Linux (Ultra-light, ~270 KB RAM)",
        "releases": [
            {"id": "3.24", "name": "3.24 (Current Stable - Recommended)"},
            {"id": "3.23", "name": "3.23"},
            {"id": "3.22", "name": "3.22"},
            {"id": "3.21", "name": "3.21"},
            {"id": "edge", "name": "edge (Rolling release)"}
        ]
    },
    "debian": {
        "name": "Debian GNU/Linux (apt / glibc, ~1.5 MB RAM)",
        "releases": [
            {"id": "bookworm", "name": "12 Bookworm (Stable - Recommended)"},
            {"id": "trixie", "name": "13 Trixie (Testing)"},
            {"id": "bullseye", "name": "11 Bullseye (Oldstable)"},
            {"id": "sid", "name": "sid (Unstable)"}
        ]
    },
    "ubuntu": {
        "name": "Ubuntu Linux (Canonical LTS)",
        "releases": [
            {"id": "noble", "name": "24.04 LTS Noble Numbat (Latest LTS)"},
            {"id": "jammy", "name": "22.04 LTS Jammy Jellyfish"},
            {"id": "focal", "name": "20.04 LTS Focal Fossa"}
        ]
    },
    "archlinux": {
        "name": "Arch Linux (Rolling, bleeding-edge)",
        "releases": [
            {"id": "current", "name": "current (Daily Rolling Release)"}
        ]
    },
    "fedora": {
        "name": "Fedora Linux",
        "releases": [
            {"id": "41", "name": "41 (Latest)"},
            {"id": "40", "name": "40"},
            {"id": "39", "name": "39"}
        ]
    },
    "rockylinux": {
        "name": "Rocky Linux (RHEL Enterprise Compatible)",
        "releases": [
            {"id": "9", "name": "9 (Enterprise 9)"},
            {"id": "8", "name": "8 (Enterprise 8)"}
        ]
    },
    "almalinux": {
        "name": "AlmaLinux (RHEL Enterprise Compatible)",
        "releases": [
            {"id": "10", "name": "10"},
            {"id": "9", "name": "9"},
            {"id": "8", "name": "8"}
        ]
    }
}

def run_cmd(cmd, timeout=30):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)

def parse_container_info(name):
    code, out, _ = run_cmd(["lxc-info", "-n", name], timeout=5)
    data = {
        "name": name,
        "state": "STOPPED",
        "pid": "-",
        "ip": "-",
        "memory": "-",
        "cpu": "-",
        "autostart": False,
        "net_mode": "nat"
    }
    
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("State:"):
            data["state"] = line.split(":", 1)[1].strip()
        elif line.startswith("PID:"):
            data["pid"] = line.split(":", 1)[1].strip()
        elif line.startswith("IP:"):
            ip_val = line.split(":", 1)[1].strip()
            # prefer non-loopback
            if ip_val and not ip_val.startswith("127."):
                if data["ip"] == "-":
                    data["ip"] = ip_val
                else:
                    data["ip"] += f", {ip_val}"
        elif line.startswith("Memory use:"):
            data["memory"] = line.split(":", 1)[1].strip()
        elif line.startswith("CPU use:"):
            data["cpu"] = line.split(":", 1)[1].strip()

    cfg_file = os.path.join(LXC_DIR, name, "config")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                content = f.read()
                if re.search(r"^\s*lxc\.start\.auto\s*=\s*1", content, re.M):
                    data["autostart"] = True
                if re.search(r"^\s*lxc\.net\.0\.link\s*=\s*br0", content, re.M):
                    data["net_mode"] = "lan"
                else:
                    data["net_mode"] = "nat"
                if data["ip"] == "-":
                    m = re.search(r"^\s*lxc\.net\.0\.ipv4\.address\s*=\s*([0-9\.]+)", content, re.M)
                    if m:
                        data["ip"] = m.group(1)
        except Exception:
            pass

    return data

def get_containers():
    code, out, _ = run_cmd(["lxc-ls", "-1"])
    names = [n.strip() for n in out.splitlines() if n.strip() and n.strip() not in ["cache", "snaps"]]
    containers = [parse_container_info(name) for name in sorted(names)]
    return containers

def get_host_metrics():
    try:
        load = list(os.getloadavg())
    except Exception:
        load = [0.0, 0.0, 0.0]

    mem_total = 0
    mem_avail = 0
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1]) // 1024
    except Exception:
        pass
    mem_used = max(0, mem_total - mem_avail)
    mem_pct = round((mem_used / mem_total * 100), 1) if mem_total else 0

    disk_total = 0
    disk_used = 0
    disk_free = 0
    disk_pct = 0
    try:
        total, used, free = shutil.disk_usage(LXC_DIR if os.path.exists(LXC_DIR) else "/")
        disk_total = round(total / (1024**3), 1)
        disk_used = round(used / (1024**3), 1)
        disk_free = round(free / (1024**3), 1)
        disk_pct = round((used / total * 100), 1) if total else 0
    except Exception:
        pass

    containers = get_containers()
    counts = {
        "total": len(containers),
        "running": sum(1 for c in containers if c["state"] == "RUNNING"),
        "stopped": sum(1 for c in containers if c["state"] == "STOPPED"),
        "frozen": sum(1 for c in containers if c["state"] == "FROZEN"),
    }

    used_octets = []
    for c in containers:
        if c.get("ip") and "10.0.3." in c["ip"]:
            for part in c["ip"].split(","):
                part = part.strip()
                if part.startswith("10.0.3."):
                    try:
                        used_octets.append(int(part.split(".")[-1]))
                    except ValueError:
                        pass
    next_octet = 10
    while next_octet in used_octets and next_octet < 254:
        next_octet += 1
    suggested_ip = f"10.0.3.{next_octet}"

    return {
        "load": [round(x, 2) for x in load],
        "ram": {
            "total_mb": mem_total,
            "used_mb": mem_used,
            "free_mb": mem_avail,
            "percent": mem_pct
        },
        "disk": {
            "total_gb": disk_total,
            "used_gb": disk_used,
            "free_gb": disk_free,
            "percent": disk_pct
        },
        "counts": counts,
        "suggested_ip": suggested_ip
    }

# -----------------------------------------------------------------------------
# REST API Endpoints
# -----------------------------------------------------------------------------

@app.route("/api/status", method="GET")
def api_status():
    response.content_type = "application/json"
    return get_host_metrics()

@app.route("/api/catalog", method="GET")
def api_catalog():
    response.content_type = "application/json"
    # Check what images are already cached in /var/cache/lxc/download
    cached_images = []
    dl_cache = os.path.join(CACHE_DIR, "download")
    if os.path.exists(dl_cache):
        try:
            for dist in os.listdir(dl_cache):
                dpath = os.path.join(dl_cache, dist)
                if os.path.isdir(dpath):
                    for rel in os.listdir(dpath):
                        cached_images.append(f"{dist}:{rel}")
        except Exception:
            pass
    if os.path.exists(MINIROOTFS_CACHE):
        cached_images.append("alpine:3.24")

    return {
        "catalog": DISTRO_CATALOG,
        "cached": list(set(cached_images))
    }

TEMPLATE_TASK = {
    "status": "idle",
    "distro": "",
    "release": "",
    "message": ""
}

@app.route("/api/templates/download", method="POST")
def api_template_download():
    response.content_type = "application/json"
    global TEMPLATE_TASK
    if TEMPLATE_TASK["status"] == "running":
        return {"success": False, "error": f"Уже выполняется загрузка {TEMPLATE_TASK['distro']} {TEMPLATE_TASK['release']}"}

    data = request.json or {}
    distro = data.get("distro", "").strip().lower()
    release = data.get("release", "").strip()
    if not distro or not release:
        return {"success": False, "error": "Не указан дистрибутив или релиз."}

    TEMPLATE_TASK = {
        "status": "running",
        "distro": distro,
        "release": release,
        "message": f"Загрузка шаблона {distro} {release}..."
    }

    def _worker():
        global TEMPLATE_TASK
        try:
            if distro == "alpine" and release in ["3.24", "current", "latest"]:
                os.makedirs(os.path.join(LXC_DIR, "cache"), exist_ok=True)
                url = os.environ.get("MINIROOTFS_URL", "https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64/alpine-minirootfs-3.24.0-x86_64.tar.gz")
                cmd = ["curl", "-fL", "--retry", "3", "--connect-timeout", "15", url, "-o", MINIROOTFS_CACHE]
                code, out, err = run_cmd(cmd, timeout=120)
                if code == 0 and os.path.exists(MINIROOTFS_CACHE) and os.path.getsize(MINIROOTFS_CACHE) > 0:
                    TEMPLATE_TASK["status"] = "success"
                    TEMPLATE_TASK["message"] = f"Шаблон Alpine {release} успешно загружен в кэш!"
                else:
                    TEMPLATE_TASK["status"] = "error"
                    TEMPLATE_TASK["message"] = f"Ошибка загрузки Alpine minirootfs: {err or out}"
            else:
                tmp_cname = f"tmpl-dl-{distro}-{release}".replace(".", "-")
                cmd = ["lxc-create", "-n", tmp_cname, "-t", "download", "--", "-d", distro, "-r", release, "-a", "amd64"]
                code, out, err = run_cmd(cmd, timeout=600)
                run_cmd(["lxc-destroy", "-n", tmp_cname, "-f"], timeout=30)
                if code == 0:
                    TEMPLATE_TASK["status"] = "success"
                    TEMPLATE_TASK["message"] = f"Шаблон {distro} {release} успешно загружен и закэширован!"
                else:
                    TEMPLATE_TASK["status"] = "error"
                    TEMPLATE_TASK["message"] = f"Ошибка загрузки {distro} {release}: {err or out}"
        except Exception as ex:
            TEMPLATE_TASK["status"] = "error"
            TEMPLATE_TASK["message"] = f"Исключение при загрузке: {str(ex)}"

    import threading
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "message": f"Начата фоновая загрузка шаблона {distro} {release}."}

@app.route("/api/templates/status", method="GET")
def api_template_status():
    response.content_type = "application/json"
    return TEMPLATE_TASK

@app.route("/api/templates/delete", method="POST")
def api_template_delete():
    response.content_type = "application/json"
    data = request.json or {}
    distro = data.get("distro", "").strip().lower()
    release = data.get("release", "").strip()
    if not distro or not release:
        return {"success": False, "error": "Не указан дистрибутив или релиз."}

    try:
        deleted = False
        if distro == "alpine" and release in ["3.24", "current", "latest"]:
            if os.path.exists(MINIROOTFS_CACHE):
                os.remove(MINIROOTFS_CACHE)
                deleted = True
        cpath = os.path.join(CACHE_DIR, "download", distro, release)
        if os.path.exists(cpath):
            shutil.rmtree(cpath, ignore_errors=True)
            deleted = True
        if deleted:
            return {"success": True, "message": f"Кэш шаблона {distro} {release} удален."}
        else:
            return {"success": False, "error": f"Шаблон {distro} {release} не найден в кэше."}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route("/api/containers", method="GET")
def api_containers():
    response.content_type = "application/json"
    return {"containers": get_containers()}

@app.route("/api/containers/create", method="POST")
def api_create():
    response.content_type = "application/json"
    data = request.json or {}
    name = data.get("name", "").strip()
    clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "-", name).strip("-").lower()
    distro = data.get("distro", "alpine").strip().lower()
    release = data.get("release", "3.24").strip()
    net_mode = data.get("net_mode", "lan").strip().lower()
    ip = data.get("ip", "").strip()
    autostart = bool(data.get("autostart", False))
    start_now = bool(data.get("start_now", True))

    if not clean_name or len(clean_name) < 2 or len(clean_name) > 32:
        return {"success": False, "error": "Имя контейнера должно содержать от 2 до 32 символов (латинские буквы a-z, цифры, дефис или подчеркивание)."}
    name = clean_name

    target_dir = os.path.join(LXC_DIR, name)
    if os.path.exists(target_dir):
        return {"success": False, "error": f"Контейнер '{name}' уже существует."}

    # Decide creation method: Fast Local Cache vs Official lxc-download
    rootfs_dir = os.path.join(target_dir, "rootfs")
    is_fast_clone = (distro == "alpine" and release in ["3.24", "current", "latest"] and os.path.exists(MINIROOTFS_CACHE))

    try:
        if is_fast_clone:
            # 1. Instant local minirootfs cloning (~1.1s)
            os.makedirs(rootfs_dir, exist_ok=True)
            code, _, err = run_cmd(["tar", "-xzf", MINIROOTFS_CACHE, "-C", rootfs_dir], timeout=30)
            if code != 0:
                shutil.rmtree(target_dir, ignore_errors=True)
                return {"success": False, "error": "Ошибка распаковки rootfs: " + err}

            # Prepare base config
            if net_mode == "lan":
                net_cfg = """lxc.net.0.type = veth
lxc.net.0.flags = up
lxc.net.0.link = br0
lxc.net.0.name = eth0"""
            else:
                if not re.match(r"^10\.0\.3\.\d{1,3}$", ip):
                    ip = "10.0.3.10"
                net_cfg = f"""lxc.net.0.type = veth
lxc.net.0.flags = up
lxc.net.0.link = lxcbr0
lxc.net.0.name = eth0
lxc.net.0.ipv4.address = {ip}/24
lxc.net.0.ipv4.gateway = 10.0.3.1"""

            config_content = f"""lxc.uts.name = {name}
lxc.rootfs.path = dir:{rootfs_dir}
{net_cfg}
lxc.tty.max = 4
lxc.pty.max = 1024
lxc.mount.auto = proc:mixed sys:ro cgroup:mixed
lxc.start.auto = {"1" if autostart else "0"}
"""
            with open(os.path.join(target_dir, "config"), "w", encoding="utf-8") as f:
                f.write(config_content)

            etc_dir = os.path.join(rootfs_dir, "etc")
            os.makedirs(etc_dir, exist_ok=True)

            if net_mode == "lan":
                # Container will use router DHCP
                net_if_dir = os.path.join(etc_dir, "network")
                os.makedirs(net_if_dir, exist_ok=True)
                with open(os.path.join(net_if_dir, "interfaces"), "w", encoding="utf-8") as f:
                    f.write("auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n")
                inittab_path = os.path.join(etc_dir, "inittab")
                if os.path.exists(inittab_path):
                    with open(inittab_path, "a", encoding="utf-8") as f:
                        f.write("\n::sysinit:/sbin/udhcpc -b -i eth0\n")
            else:
                with open(os.path.join(etc_dir, "resolv.conf"), "w", encoding="utf-8") as f:
                    f.write("nameserver 10.0.3.1\nnameserver 1.1.1.1\n")

            if os.path.exists("/etc/apk/repositories"):
                apk_dir = os.path.join(etc_dir, "apk")
                os.makedirs(apk_dir, exist_ok=True)
                shutil.copy2("/etc/apk/repositories", os.path.join(apk_dir, "repositories"))

        else:
            # 2. Official lxc-download template from images.linuxcontainers.org
            code, out, err = run_cmd([
                "lxc-create", "-n", name, "-t", "download", "--",
                "-d", distro, "-r", release, "-a", "amd64"
            ], timeout=300)
            if code != 0:
                shutil.rmtree(target_dir, ignore_errors=True)
                return {"success": False, "error": f"Ошибка создания контейнера (lxc-download): {err or out}"}

            # Update container configuration for network mode
            cfg_path = os.path.join(target_dir, "config")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                for line in lines:
                    if not line.strip().startswith("lxc.net.") and not line.strip().startswith("lxc.start.auto"):
                        new_lines.append(line)

                if net_mode == "lan":
                    new_lines.append("\n# Network Mode: Home LAN (br0 - DHCP from router)\n")
                    new_lines.append("lxc.net.0.type = veth\n")
                    new_lines.append("lxc.net.0.flags = up\n")
                    new_lines.append("lxc.net.0.link = br0\n")
                    new_lines.append("lxc.net.0.name = eth0\n")
                else:
                    if not re.match(r"^10\.0\.3\.\d{1,3}$", ip):
                        ip = "10.0.3.10"
                    new_lines.append("\n# Network Mode: NAT Sandbox (lxcbr0)\n")
                    new_lines.append("lxc.net.0.type = veth\n")
                    new_lines.append("lxc.net.0.flags = up\n")
                    new_lines.append("lxc.net.0.link = lxcbr0\n")
                    new_lines.append("lxc.net.0.name = eth0\n")
                    new_lines.append(f"lxc.net.0.ipv4.address = {ip}/24\n")
                    new_lines.append("lxc.net.0.ipv4.gateway = 10.0.3.1\n")

                new_lines.append(f"lxc.start.auto = {'1' if autostart else '0'}\n")
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

            # Pre-configure network inside container if possible
            if net_mode == "nat" and os.path.exists(rootfs_dir):
                etc_dir = os.path.join(rootfs_dir, "etc")
                os.makedirs(etc_dir, exist_ok=True)
                with open(os.path.join(etc_dir, "resolv.conf"), "w", encoding="utf-8") as f:
                    f.write("nameserver 10.0.3.1\nnameserver 1.1.1.1\n")

        if start_now:
            s_code, _, s_err = run_cmd(["lxc-start", "-n", name, "-d"])
            if s_code != 0:
                return {"success": True, "message": f"Контейнер '{name}' создан, но не смог запуститься автоматически: {s_err}", "warning": s_err}

        return {"success": True, "message": f"Контейнер '{name}' ({distro} {release}) успешно создан и запущен!"}

    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"success": False, "error": str(e)}

@app.route("/api/containers/<name>/action", method="POST")
def api_action(name):
    response.content_type = "application/json"
    data = request.json or {}
    action = data.get("action", "").strip().lower()

    if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
        return {"success": False, "error": "Invalid container name."}

    target_dir = os.path.join(LXC_DIR, name)
    if not os.path.exists(target_dir):
        return {"success": False, "error": "Container does not exist."}

    if action == "start":
        code, out, err = run_cmd(["lxc-start", "-n", name, "-d"])
        return {"success": code == 0, "message": "Container started" if code == 0 else err}

    elif action == "stop":
        code, out, err = run_cmd(["lxc-stop", "-n", name, "-k"])
        return {"success": code == 0, "message": "Container stopped" if code == 0 else err}

    elif action == "restart":
        run_cmd(["lxc-stop", "-n", name, "-k"])
        time.sleep(0.5)
        code, out, err = run_cmd(["lxc-start", "-n", name, "-d"])
        return {"success": code == 0, "message": "Container restarted" if code == 0 else err}

    elif action == "freeze":
        code, out, err = run_cmd(["lxc-freeze", "-n", name])
        return {"success": code == 0, "message": "Container frozen" if code == 0 else err}

    elif action == "unfreeze":
        code, out, err = run_cmd(["lxc-unfreeze", "-n", name])
        return {"success": code == 0, "message": "Container unfrozen" if code == 0 else err}

    elif action == "destroy":
        run_cmd(["lxc-stop", "-n", name, "-k"])
        # Destroy all snapshots first to avoid LXC 7.0 blocking container deletion
        run_cmd(["lxc-snapshot", "-n", name, "-d", "ALL"])
        code, out, err = run_cmd(["lxc-destroy", "-n", name, "-f"])
        return {"success": code == 0, "message": "Container destroyed" if code == 0 else err}

    elif action == "toggle_autostart":
        cfg_file = os.path.join(target_dir, "config")
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                content = f.read()
            if re.search(r"^\s*lxc\.start\.auto\s*=\s*1", content, re.M):
                content = re.sub(r"^\s*lxc\.start\.auto\s*=\s*1", "lxc.start.auto = 0", content, flags=re.M)
                state = False
            elif re.search(r"^\s*lxc\.start\.auto\s*=\s*0", content, re.M):
                content = re.sub(r"^\s*lxc\.start\.auto\s*=\s*0", "lxc.start.auto = 1", content, flags=re.M)
                state = True
            else:
                content += "\nlxc.start.auto = 1\n"
                state = True
            with open(cfg_file, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "autostart": state}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": "Unknown action: " + action}

# -----------------------------------------------------------------------------
# Snapshot Management Endpoints (lxc-snapshot)
# -----------------------------------------------------------------------------

@app.route("/api/containers/<name>/snapshots", method="GET")
def api_snapshots_list(name):
    response.content_type = "application/json"
    code, out, _ = run_cmd(["lxc-snapshot", "-n", name, "-L", "-C"])
    snapshots = []
    # Output format: snap_name (path) date_time [comment]
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("No snapshots"):
            continue
        parts = line.split(None, 3)
        if len(parts) >= 3:
            s_name = parts[0]
            s_date = parts[2]
            s_comment = parts[3] if len(parts) > 3 else ""
            snapshots.append({
                "name": s_name,
                "date": s_date,
                "comment": s_comment
            })
    return {"snapshots": snapshots}

@app.route("/api/containers/<name>/snapshots/create", method="POST")
def api_snapshots_create(name):
    response.content_type = "application/json"
    data = request.json or {}
    comment = data.get("comment", "").strip() or "Snapshot created via Web UI"

    # Directory backend requires container to be stopped to create a consistent rsync snapshot
    c_info = parse_container_info(name)
    was_running = (c_info.get("state") == "RUNNING")
    if was_running:
        run_cmd(["lxc-stop", "-n", name, "-k"])

    comment_file = f"/tmp/lxc-snap-comment-{name}-{int(time.time())}.txt"
    try:
        with open(comment_file, "w", encoding="utf-8") as f:
            f.write(comment + "\n")
        code, out, err = run_cmd(["lxc-snapshot", "-n", name, "-c", comment_file])
    finally:
        if os.path.exists(comment_file):
            os.remove(comment_file)
        if was_running:
            run_cmd(["lxc-start", "-n", name, "-d"])

    if code == 0:
        return {"success": True, "message": "Snapshot created successfully!"}
    return {"success": False, "error": err or out}

@app.route("/api/containers/<name>/snapshots/restore", method="POST")
def api_snapshots_restore(name):
    response.content_type = "application/json"
    data = request.json or {}
    snap_name = data.get("snapshot", "").strip()
    new_name = data.get("new_name", "").strip()

    if not snap_name:
        return {"success": False, "error": "Snapshot name is required."}

    # Stop container if running
    run_cmd(["lxc-stop", "-n", name, "-k"])

    cmd = ["lxc-snapshot", "-n", name, "-r", snap_name]
    if new_name:
        cmd.extend(["-N", new_name])
    else:
        cmd.extend(["-N", name])

    code, out, err = run_cmd(cmd)
    if code == 0:
        # Start container back up
        target_to_start = new_name if new_name else name
        run_cmd(["lxc-start", "-n", target_to_start, "-d"])
        return {"success": True, "message": f"Successfully restored '{target_to_start}' from '{snap_name}'!"}
    return {"success": False, "error": err or out}

@app.route("/api/containers/<name>/snapshots/destroy", method="POST")
def api_snapshots_destroy(name):
    response.content_type = "application/json"
    data = request.json or {}
    snap_name = data.get("snapshot", "").strip()

    if not snap_name:
        return {"success": False, "error": "Snapshot name is required."}

    code, out, err = run_cmd(["lxc-snapshot", "-n", name, "-d", snap_name])
    if code == 0:
        return {"success": True, "message": f"Snapshot '{snap_name}' deleted."}
    return {"success": False, "error": err or out}

# -----------------------------------------------------------------------------
# Quick Command Runner Exec Endpoint (lxc-attach)
# -----------------------------------------------------------------------------

@app.route("/api/containers/<name>/exec", method="POST")
def api_exec(name):
    response.content_type = "application/json"
    data = request.json or {}
    cmd = data.get("command", "").strip()

    if not cmd:
        return {"success": False, "error": "Command cannot be empty."}

    t0 = time.time()
    code, out, err = run_cmd(["lxc-attach", "-n", name, "--", "sh", "-c", cmd], timeout=20)
    dur_ms = round((time.time() - t0) * 1000)

    combined_output = out
    if err:
        if combined_output:
            combined_output += "\n" + err
        else:
            combined_output = err

    return {
        "success": True,
        "exit_code": code,
        "output": combined_output,
        "duration_ms": dur_ms
    }

# -----------------------------------------------------------------------------
# Modern Single-Page Dashboard UI
# -----------------------------------------------------------------------------
INDEX_HTML = """<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📦 LXC Sandbox Manager</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        brand: { 500: '#3b82f6', 600: '#2563eb', 700: '#1d4ed8' },
                        slate: { 850: '#151e2e', 900: '#0f172a', 950: '#020617' }
                    }
                }
            }
        }
    </script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600;700&display=swap');
        body { font-family: 'Inter', sans-serif; }
        .font-mono { font-family: 'JetBrains Mono', monospace; }
        .pulse-dot { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col antialiased selection:bg-brand-500 selection:text-white">

    <!-- Top Navigation Header -->
    <header class="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-40 px-6 py-4">
        <div class="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 to-cyan-500 flex items-center justify-center shadow-lg shadow-brand-500/20">
                    <span class="text-xl">📦</span>
                </div>
                <div>
                    <h1 class="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                        LXC Sandbox Manager
                        <span class="text-xs bg-brand-500/20 text-brand-400 border border-brand-500/30 px-2 py-0.5 rounded-full font-mono">Alpine 3.24</span>
                    </h1>
                    <p class="text-xs text-slate-400">Управление песочницей, снимками и терминалами контейнеров</p>
                </div>
            </div>

            <div class="flex items-center gap-3">
                <button onclick="openTemplatesModal()" class="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-sm px-3.5 py-2 rounded-lg transition active:scale-95 shadow-sm">
                    <span>📥</span> Шаблоны ОС
                </button>
                <button onclick="openCreateModal()" class="flex items-center gap-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium text-sm px-4 py-2 rounded-lg shadow-md shadow-blue-600/20 transition-all active:scale-95">
                    <span>⚡</span> Создать Sandbox
                </button>
                <button onclick="refreshData()" class="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition" title="Обновить">
                    <svg class="w-5 h-5" id="refresh-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                </button>
            </div>
        </div>
    </header>

    <!-- Main Content Area -->
    <main class="max-w-7xl mx-auto w-full px-6 py-6 flex-1 space-y-6">

        <!-- Host Metrics Bar -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
            <!-- CPU Load -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
                <div class="flex items-center justify-between text-xs text-slate-400 mb-2">
                    <span>Нагрузка CPU (1, 5, 15m)</span>
                    <span class="text-blue-400 font-mono">LoadAvg</span>
                </div>
                <div id="stat-load" class="text-xl font-bold font-mono text-white">0.00, 0.00, 0.00</div>
                <div class="text-xs text-slate-500 mt-1">Ядер хоста: активны</div>
            </div>

            <!-- Host RAM -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
                <div class="flex items-center justify-between text-xs text-slate-400 mb-2">
                    <span>Оперативная память хоста</span>
                    <span id="stat-ram-pct" class="text-emerald-400 font-mono">0%</span>
                </div>
                <div id="stat-ram-text" class="text-xl font-bold font-mono text-white">0 / 0 MB</div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 mt-2 overflow-hidden">
                    <div id="stat-ram-bar" class="bg-emerald-500 h-1.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
            </div>

            <!-- LXC Storage -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
                <div class="flex items-center justify-between text-xs text-slate-400 mb-2">
                    <span>Диск LXC (/var/lib/lxc)</span>
                    <span id="stat-disk-pct" class="text-cyan-400 font-mono">0%</span>
                </div>
                <div id="stat-disk-text" class="text-xl font-bold font-mono text-white">0 / 0 GB</div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 mt-2 overflow-hidden">
                    <div id="stat-disk-bar" class="bg-cyan-500 h-1.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
            </div>

            <!-- Sandbox Counts -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm">
                <div class="flex items-center justify-between text-xs text-slate-400 mb-2">
                    <span>Контейнеры Sandbox</span>
                    <span class="text-purple-400 font-mono">LXC Total</span>
                </div>
                <div class="flex items-baseline gap-2">
                    <span id="stat-active" class="text-2xl font-bold text-emerald-400">0</span>
                    <span class="text-xs text-slate-400">работает из</span>
                    <span id="stat-total" class="text-xl font-bold text-white">0</span>
                </div>
                <div class="text-xs text-slate-500 mt-1">Остановлено: <span id="stat-stopped">0</span></div>
            </div>
        </div>

        <!-- Containers Section -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
            <div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between">
                <div>
                    <h2 class="font-bold text-white text-base">Тестовые контейнеры</h2>
                    <p class="text-xs text-slate-400">Поддержка Alpine, Debian, Ubuntu, снимков (снапшотов) и веб-терминала</p>
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-xs text-slate-400">Автообновление:</span>
                    <button id="auto-btn" onclick="toggleAutoRefresh()" class="px-2.5 py-1 text-xs rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">Вкл (4с)</button>
                </div>
            </div>

            <div class="overflow-x-auto">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-950/60 text-slate-400 text-xs uppercase tracking-wider border-b border-slate-800">
                            <th class="py-3 px-6">Контейнер</th>
                            <th class="py-3 px-6">Статус</th>
                            <th class="py-3 px-6">Сеть / IP</th>
                            <th class="py-3 px-6">Память / RAM</th>
                            <th class="py-3 px-6">CPU Время</th>
                            <th class="py-3 px-6">Автостарт</th>
                            <th class="py-3 px-6 text-right">Действия</th>
                        </tr>
                    </thead>
                    <tbody id="containers-table" class="divide-y divide-slate-800 text-sm">
                        <tr>
                            <td colspan="7" class="py-12 text-center text-slate-500">Загрузка списка контейнеров...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Guide Footer -->
        <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-6">
            <h3 class="text-sm font-bold text-white mb-2 flex items-center gap-2">
                <span>💡</span> Возможности экосистемы LXC Sandbox
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs text-slate-400">
                <div class="p-3 bg-slate-950/40 rounded-lg border border-slate-800/80">
                    <div class="font-semibold text-slate-200 mb-1">🌐 Сеть LAN (DHCP) или NAT</div>
                    Выбирайте режим LAN для получения реального IP от домашнего роутера (192.168.1.x) или режим изолированного NAT (10.0.3.x).
                </div>
                <div class="p-3 bg-slate-950/40 rounded-lg border border-slate-800/80">
                    <div class="font-semibold text-slate-200 mb-1">📸 Мгновенные снимки (Rollback)</div>
                    Фиксируйте состояние контейнера перед тестами и откатывайтесь в один клик через встроенный механизм lxc-snapshot.
                </div>
                <div class="p-3 bg-slate-950/40 rounded-lg border border-slate-800/80">
                    <div class="font-semibold text-slate-200 mb-1">📟 Полноценный Web Terminal (ttyd)</div>
                    Интерактивный шелл xterm.js прямо в браузере с поддержкой htop, цветов, стрелочек, автодополнения Tab и Ctrl+C.
                </div>
            </div>
        </div>

    </main>

    <!-- Create Container Modal -->
    <div id="create-modal" class="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4 max-h-[92vh] overflow-y-auto">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 class="font-bold text-white text-lg flex items-center gap-2">
                    <span>⚡</span> Новый Sandbox Контейнер
                </h3>
                <button onclick="closeCreateModal()" class="text-slate-400 hover:text-white">&times;</button>
            </div>

            <form id="create-form" onsubmit="handleCreate(event)" class="space-y-4 text-sm">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Имя контейнера</label>
                    <input type="text" id="new-name" required placeholder="например: test-sandbox, dev-node-1" oninput="this.value = this.value.replace(/[^a-zA-Z0-9_\\-]/g, '').toLowerCase()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-xs">
                    <p class="text-[11px] text-slate-500 mt-1">Только латинские буквы a-z, цифры и дефис (2-32 символа).</p>
                </div>

                <!-- Cascade Distro & Version Select -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-medium text-slate-300 mb-1">Дистрибутив (ОС)</label>
                        <select id="new-distro" onchange="updateReleaseDropdown()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs">
                            <option value="alpine">Alpine Linux (~270 KB RAM)</option>
                            <option value="debian">Debian GNU/Linux (~1.5 MB RAM)</option>
                            <option value="ubuntu">Ubuntu Linux (LTS)</option>
                            <option value="archlinux">Arch Linux (Rolling)</option>
                            <option value="fedora">Fedora Linux</option>
                            <option value="rockylinux">Rocky Linux (RHEL 9)</option>
                            <option value="custom">Ручной ввод...</option>
                        </select>
                    </div>

                    <div>
                        <label class="block text-xs font-medium text-slate-300 mb-1">Версия / Релиз</label>
                        <select id="new-release" onchange="updateCacheHint()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs font-mono">
                            <!-- Populated dynamically via JS -->
                        </select>
                    </div>
                </div>
                <div id="distro-cache-hint" class="text-xs pt-1"></div>

                <!-- Custom Distro/Release fields (hidden by default) -->
                <div id="custom-fields" class="grid grid-cols-2 gap-3 hidden p-3 bg-slate-950/60 rounded-lg border border-slate-800">
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Имя дистрибутива</label>
                        <input type="text" id="custom-distro-input" placeholder="kali, voidlinux, etc." class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-400 mb-1">Релиз</label>
                        <input type="text" id="custom-release-input" placeholder="current, 40, etc." class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white">
                    </div>
                </div>

                <!-- Network Mode Radio -->
                <div class="p-3 bg-slate-950/80 rounded-xl border border-slate-800 space-y-2">
                    <label class="block text-xs font-semibold text-white">Режим сети (Network Mode):</label>
                    <label class="flex items-start gap-2.5 cursor-pointer">
                        <input type="radio" name="net_mode" value="lan" checked onchange="toggleNetModeUI()" class="mt-0.5 text-brand-600 bg-slate-900 border-slate-700 focus:ring-0">
                        <div>
                            <span class="text-xs font-medium text-slate-200">Домашняя сеть LAN (br0 - DHCP от роутера)</span>
                            <p class="text-[11px] text-slate-400">Контейнер получит реальный домашний IP (192.168.1.X) и будет виден как отдельный ПК в роутере.</p>
                        </div>
                    </label>
                    <label class="flex items-start gap-2.5 cursor-pointer pt-1">
                        <input type="radio" name="net_mode" value="nat" onchange="toggleNetModeUI()" class="mt-0.5 text-brand-600 bg-slate-900 border-slate-700 focus:ring-0">
                        <div>
                            <span class="text-xs font-medium text-slate-200">Изолированная песочница NAT (lxcbr0 - 10.0.3.x)</span>
                            <p class="text-[11px] text-slate-400">Закрытый полигон за файрволом, не засоряет DHCP-таблицу домашнего роутера.</p>
                        </div>
                    </label>
                </div>

                <div id="nat-ip-container" class="hidden">
                    <label class="block text-xs font-medium text-slate-300 mb-1">Статический IPv4 для NAT (lxcbr0)</label>
                    <input type="text" id="new-ip" placeholder="10.0.3.10" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-xs">
                </div>

                <div class="space-y-2 pt-2 border-t border-slate-800">
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" id="new-autostart" class="rounded bg-slate-950 border-slate-700 text-brand-600 focus:ring-0">
                        <span class="text-xs text-slate-300">Автозапуск при старте хоста (lxc.start.auto)</span>
                    </label>
                    <label class="flex items-center gap-2 cursor-pointer">
                        <input type="checkbox" id="new-startnow" checked class="rounded bg-slate-950 border-slate-700 text-brand-600 focus:ring-0">
                        <span class="text-xs text-slate-300">Запустить контейнер сразу после развертывания</span>
                    </label>
                </div>

                <div id="create-progress" class="hidden p-3 bg-brand-950/40 border border-brand-500/30 rounded-xl space-y-2">
                    <div class="flex items-center gap-2 text-xs text-brand-300 font-medium">
                        <svg class="animate-spin w-4 h-4 text-brand-400" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
                        <span id="create-progress-text">Развертывание контейнера... Пожалуйста, подождите.</span>
                    </div>
                    <p class="text-[11px] text-slate-400">Для Alpine minirootfs создание занимает ~1 секунду. При первой загрузке других ОС (Debian, Ubuntu) скачивание образа может занять до 1-2 минут.</p>
                </div>

                <div class="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
                    <button type="button" id="btn-create-cancel" onclick="closeCreateModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Отмена</button>
                    <button type="submit" id="btn-create-submit" class="px-5 py-2 bg-brand-600 hover:bg-brand-500 text-white font-medium rounded-lg shadow-md transition text-xs">Создать Sandbox</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Templates Catalog Modal -->
    <div id="templates-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-2xl w-full p-6 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <h3 class="font-bold text-white text-base flex items-center gap-2">
                    <span>📥</span> Каталог Шаблонов ОС (LXC Images)
                </h3>
                <button onclick="closeTemplatesModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <div id="template-dl-banner" class="hidden p-3.5 bg-blue-950/40 border border-blue-500/30 rounded-xl space-y-1">
                <div class="flex items-center justify-between text-xs">
                    <span id="template-dl-text" class="text-blue-300 font-medium flex items-center gap-2">
                        <svg class="animate-spin w-4 h-4 text-blue-400 inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
                        Загрузка шаблона...
                    </span>
                    <span class="px-2 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-300 border border-blue-500/30 font-mono">В процессе</span>
                </div>
                <p class="text-[11px] text-slate-400">Шаблон загружается в кэш хоста (/var/cache/lxc). Закрытие окна не прервет фоновую загрузку.</p>
            </div>

            <div class="space-y-3 overflow-y-auto flex-1 pr-1" id="templates-list-container">
                <!-- Populated dynamically via JS -->
            </div>

            <div class="flex items-center justify-between pt-3 border-t border-slate-800 text-xs text-slate-500">
                <span>Кэш: <code class="text-slate-400 font-mono">/var/cache/lxc/download</code></span>
                <button type="button" onclick="closeTemplatesModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Закрыть</button>
            </div>
        </div>
    </div>
    <div id="snapshot-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-xl w-full p-6 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-xl">📸</span>
                    <h3 class="font-bold text-white text-base">
                        Снимки (Snapshots): <span id="snap-target-name" class="text-brand-400 font-mono">name</span>
                    </h3>
                </div>
                <button onclick="closeSnapshotModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <!-- Create Snapshot Form -->
            <form onsubmit="handleCreateSnapshot(event)" class="flex gap-2">
                <input type="text" id="snap-comment-input" placeholder="Комментарий к снимку (например, перед установкой БД)..." class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs">
                <button type="submit" id="btn-snap-create" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg text-xs whitespace-nowrap transition">+ Сделать снимок</button>
            </form>

            <!-- Snapshots List -->
            <div class="flex-1 overflow-y-auto space-y-2 min-h-[160px]" id="snapshots-list">
                <div class="text-center py-8 text-slate-500 text-xs">Загрузка снимков...</div>
            </div>
        </div>
    </div>

    <!-- Full Interactive Terminal Modal (ttyd) -->
    <div id="term-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-5xl h-[85vh] shadow-2xl flex flex-col overflow-hidden">
            <div class="flex items-center justify-between border-b border-slate-800 px-5 py-3 bg-slate-950">
                <div class="flex items-center gap-3">
                    <span class="text-lg">📟</span>
                    <h3 class="font-bold text-white text-sm">
                        Интерактивный шелл (ttyd): <span id="term-target-name" class="text-emerald-400 font-mono">name</span>
                    </h3>
                    <span class="text-[11px] bg-slate-800 text-slate-400 px-2 py-0.5 rounded font-mono">xterm-256color</span>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="reloadTermFrame()" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded transition" title="Переподключить">🔄 Перезапуск</button>
                    <button onclick="closeTermModal()" class="text-slate-400 hover:text-white text-xl leading-none px-2">&times;</button>
                </div>
            </div>
            <div class="flex-1 bg-black relative">
                <iframe id="term-iframe" src="" class="w-full h-full border-none"></iframe>
            </div>
        </div>
    </div>

    <!-- Quick Exec Modal -->
    <div id="exec-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-3xl w-full p-6 shadow-2xl flex flex-col max-h-[90vh]">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-4">
                <div class="flex items-center gap-3">
                    <span class="text-xl">💻</span>
                    <div>
                        <h3 class="font-bold text-white text-base flex items-center gap-2">
                            Быстрый запуск команд: <span id="exec-target-name" class="text-brand-400 font-mono">name</span>
                        </h3>
                        <p class="text-xs text-slate-400">Выполнение команд через lxc-attach с захватом вывода</p>
                    </div>
                </div>
                <button onclick="closeExecModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <div class="flex flex-wrap items-center gap-2 mb-3">
                <span class="text-xs text-slate-500">Быстрые:</span>
                <button onclick="setExecCmd('uname -a')" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded border border-slate-700 font-mono">uname -a</button>
                <button onclick="setExecCmd('ip a')" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded border border-slate-700 font-mono">ip a</button>
                <button onclick="setExecCmd('ps aux')" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded border border-slate-700 font-mono">ps aux</button>
                <button onclick="setExecCmd('df -h')" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded border border-slate-700 font-mono">df -h</button>
                <button onclick="setExecCmd('ping -c 2 1.1.1.1')" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2.5 py-1 rounded border border-slate-700 font-mono">ping 1.1.1.1</button>
            </div>

            <div class="flex-1 bg-slate-950 border border-slate-800 rounded-xl p-4 font-mono text-xs overflow-y-auto min-h-[240px] text-slate-200" id="exec-output">
                <span class="text-slate-500">// Введите команду и нажмите Enter для выполнения...</span>
            </div>

            <form id="exec-form" onsubmit="handleExec(event)" class="mt-4 flex gap-2">
                <div class="relative flex-1">
                    <span class="absolute left-3 top-2.5 text-emerald-400 font-mono font-bold">$</span>
                    <input type="text" id="exec-input" placeholder="например: apt update || apk update" required class="w-full bg-slate-950 border border-slate-700 rounded-lg pl-8 pr-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-sm">
                </div>
                <button type="submit" id="btn-exec-run" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-medium rounded-lg text-sm transition">Выполнить</button>
                <button type="button" onclick="clearExecOutput()" class="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-400 rounded-lg text-sm">Очистить</button>
            </form>
        </div>
    </div>

    <!-- Notification Toast Container -->
    <div id="toast-container" class="fixed bottom-5 right-5 z-50 space-y-2"></div>

    <script>
        let autoRefreshInterval = null;
        let activeExecTarget = '';
        let activeSnapshotTarget = '';
        let activeTermTarget = '';
        let catalogData = {};
        let cachedImagesList = [];

        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            const colors = {
                success: 'bg-emerald-950/90 border-emerald-500/50 text-emerald-200',
                error: 'bg-rose-950/90 border-rose-500/50 text-rose-200',
                info: 'bg-slate-900/90 border-slate-700 text-slate-200'
            };
            toast.className = `border px-4 py-3 rounded-xl shadow-xl text-xs backdrop-blur flex items-center gap-2 transition-all duration-300 transform translate-y-2 opacity-0 ${colors[type] || colors.info}`;
            toast.innerHTML = `<span>${type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️'}</span> <span>${escapeHtml(message)}</span>`;
            container.appendChild(toast);
            setTimeout(() => { toast.classList.remove('translate-y-2', 'opacity-0'); }, 10);
            setTimeout(() => {
                toast.classList.add('opacity-0', 'translate-y-2');
                setTimeout(() => toast.remove(), 300);
            }, 3500);
        }

        async function fetchCatalog() {
            try {
                const res = await fetch('/api/catalog');
                if (!res.ok) return;
                const d = await res.json();
                catalogData = d.catalog || {};
                cachedImagesList = d.cached || [];
                updateReleaseDropdown();
            } catch (e) {
                console.error(e);
            }
        }

        function updateReleaseDropdown() {
            const distroSelect = document.getElementById('new-distro');
            const releaseSelect = document.getElementById('new-release');
            const customFields = document.getElementById('custom-fields');
            const chosen = distroSelect.value;

            if (chosen === 'custom') {
                customFields.classList.remove('hidden');
                releaseSelect.parentElement.classList.add('hidden');
                return;
            } else {
                customFields.classList.add('hidden');
                releaseSelect.parentElement.classList.remove('hidden');
            }

            const item = catalogData[chosen];
            if (!item || !item.releases) {
                releaseSelect.innerHTML = '<option value="default">default</option>';
                return;
            }

            let html = '';
            for (const r of item.releases) {
                const isCached = cachedImagesList.includes(`${chosen}:${r.id}`);
                const cacheBadge = isCached ? ' ⚡ [кэширован]' : '';
                html += `<option value="${r.id}">${r.name}${cacheBadge}</option>`;
            }
            releaseSelect.innerHTML = html;
            updateCacheHint();
        }

        function updateCacheHint() {
            const distro = document.getElementById('new-distro').value;
            const release = document.getElementById('new-release').value;
            const hint = document.getElementById('distro-cache-hint');
            if (!hint) return;
            if (distro === 'custom') {
                hint.innerHTML = '<span class="text-amber-400">⚠️ Произвольный образ — будет скачан из репозитория images.linuxcontainers.org</span>';
                return;
            }
            const isCached = cachedImagesList.includes(`${distro}:${release}`);
            if (isCached) {
                hint.innerHTML = '<span class="text-emerald-400 font-medium flex items-center gap-1">⚡ Шаблон в кэше — создание песочницы займет ~1-2 сек</span>';
            } else {
                hint.innerHTML = `<span class="text-slate-400">📥 Шаблон не в кэше — будет скачан при создании (~1-2 мин). <a href="javascript:void(0)" onclick="closeCreateModal(); openTemplatesModal();" class="text-blue-400 underline hover:text-blue-300">Открыть менеджер шаблонов</a></span>`;
            }
        }

        let templatePollTimer = null;

        function openTemplatesModal() {
            renderTemplatesCatalog();
            document.getElementById('templates-modal').classList.remove('hidden');
            pollTemplateStatus();
        }

        function closeTemplatesModal() {
            document.getElementById('templates-modal').classList.add('hidden');
            if (templatePollTimer) {
                clearInterval(templatePollTimer);
                templatePollTimer = null;
            }
        }

        function renderTemplatesCatalog() {
            const container = document.getElementById('templates-list-container');
            if (!container) return;
            let html = '';
            for (const [distKey, dist] of Object.entries(catalogData)) {
                html += `
                <div class="p-3.5 bg-slate-950/70 border border-slate-800 rounded-xl space-y-2">
                    <div class="flex items-center justify-between">
                        <span class="font-semibold text-white text-sm">${escapeHtml(dist.name)}</span>
                        <span class="text-xs text-slate-500 font-mono">${escapeHtml(distKey)}</span>
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">`;
                for (const r of (dist.releases || [])) {
                    const tag = `${distKey}:${r.id}`;
                    const isCached = cachedImagesList.includes(tag);
                    html += `
                        <div class="flex items-center justify-between p-2 bg-slate-900 border border-slate-800/80 rounded-lg text-xs">
                            <div class="flex items-center gap-2 overflow-hidden">
                                <span class="font-medium text-slate-300 truncate">${escapeHtml(r.name || r.id)}</span>
                                ${isCached ? '<span class="text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-1.5 py-0.5 rounded font-mono shrink-0">⚡ В кэше</span>' : '<span class="text-[10px] text-slate-500 shrink-0">Не скачан</span>'}
                            </div>
                            <div class="shrink-0 ml-2">
                                ${isCached ? `
                                    <button onclick="deleteTemplate('${escapeHtml(distKey)}', '${escapeHtml(r.id)}')" class="px-2 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 rounded border border-rose-800/40 text-[11px]" title="Удалить из кэша">🗑️</button>
                                ` : `
                                    <button onclick="downloadTemplate('${escapeHtml(distKey)}', '${escapeHtml(r.id)}')" class="px-2.5 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-[11px] font-medium transition flex items-center gap-1 shadow-sm">📥 Скачать</button>
                                `}
                            </div>
                        </div>`;
                }
                html += `</div></div>`;
            }
            container.innerHTML = html;
        }

        async function downloadTemplate(distro, release) {
            try {
                const res = await fetch('/api/templates/download', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ distro, release })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'info');
                    pollTemplateStatus();
                } else {
                    showToast(d.error || 'Ошибка запуска загрузки', 'error');
                }
            } catch (err) {
                showToast(`Ошибка сети: ${err.message}`, 'error');
            }
        }

        async function deleteTemplate(distro, release) {
            if (!confirm(`Удалить кэш шаблона ${distro} ${release}?`)) return;
            try {
                const res = await fetch('/api/templates/delete', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ distro, release })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    await fetchCatalog();
                    renderTemplatesCatalog();
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            }
        }

        async function pollTemplateStatus() {
            try {
                const res = await fetch('/api/templates/status');
                if (!res.ok) return;
                const d = await res.json();
                const banner = document.getElementById('template-dl-banner');
                const text = document.getElementById('template-dl-text');
                if (d.status === 'running') {
                    if (banner) banner.classList.remove('hidden');
                    if (text) text.innerHTML = `<svg class="animate-spin w-4 h-4 text-blue-400 inline mr-2" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>${escapeHtml(d.message)}`;
                    if (!templatePollTimer) {
                        templatePollTimer = setInterval(pollTemplateStatus, 2000);
                    }
                } else {
                    if (banner) banner.classList.add('hidden');
                    if (templatePollTimer) {
                        clearInterval(templatePollTimer);
                        templatePollTimer = null;
                    }
                    if (d.status === 'success' || d.status === 'error') {
                        if (d.status === 'success') showToast(d.message, 'success');
                        if (d.status === 'error') showToast(d.message, 'error');
                        d.status = 'idle';
                        await fetchCatalog();
                        renderTemplatesCatalog();
                    }
                }
            } catch (e) {}
        }

        function toggleNetModeUI() {
            const mode = document.querySelector('input[name="net_mode"]:checked').value;
            const ipContainer = document.getElementById('nat-ip-container');
            if (mode === 'nat') {
                ipContainer.classList.remove('hidden');
            } else {
                ipContainer.classList.add('hidden');
            }
        }

        async function fetchMetrics() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const d = await res.json();
                document.getElementById('stat-load').innerText = d.load.join(', ');
                document.getElementById('stat-ram-text').innerText = `${d.ram.used_mb} / ${d.ram.total_mb} MB`;
                document.getElementById('stat-ram-pct').innerText = `${d.ram.percent}%`;
                document.getElementById('stat-ram-bar').style.width = `${d.ram.percent}%`;

                document.getElementById('stat-disk-text').innerText = `${d.disk.used_gb} / ${d.disk.total_gb} GB`;
                document.getElementById('stat-disk-pct').innerText = `${d.disk.percent}%`;
                document.getElementById('stat-disk-bar').style.width = `${d.disk.percent}%`;

                document.getElementById('stat-active').innerText = d.counts.running;
                document.getElementById('stat-total').innerText = d.counts.total;
                document.getElementById('stat-stopped').innerText = d.counts.stopped;

                if (document.getElementById('new-ip') && !document.getElementById('new-ip').value) {
                    document.getElementById('new-ip').value = d.suggested_ip;
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function fetchContainers() {
            try {
                const res = await fetch('/api/containers');
                if (!res.ok) return;
                const data = await res.json();
                renderContainers(data.containers);
            } catch (e) {
                console.error(e);
            }
        }

        function renderContainers(containers) {
            const tbody = document.getElementById('containers-table');
            if (!containers || containers.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="7" class="py-12 text-center text-slate-500">
                            <div class="text-3xl mb-2">📦</div>
                            <div class="text-slate-300 font-medium">Нет созданных Sandbox контейнеров</div>
                            <div class="text-xs text-slate-500 mt-1">Нажмите «Создать Sandbox» для быстрого запуска тестового окружения</div>
                        </td>
                    </tr>`;
                return;
            }

            let html = '';
            for (const c of containers) {
                const isRunning = c.state === 'RUNNING';
                const isFrozen = c.state === 'FROZEN';
                const badgeColor = isRunning ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 
                                   isFrozen ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' :
                                   'bg-slate-800 text-slate-400 border-slate-700';

                const netModeBadge = c.net_mode === 'lan' ? 
                    '<span class="text-[10px] bg-blue-500/20 text-blue-400 border border-blue-500/30 px-1.5 py-0.5 rounded font-mono">LAN (br0)</span>' :
                    '<span class="text-[10px] bg-purple-500/20 text-purple-400 border border-purple-500/30 px-1.5 py-0.5 rounded font-mono">NAT (lxcbr0)</span>';

                html += `
                    <tr class="hover:bg-slate-850/50 transition">
                        <td class="py-3 px-6 font-semibold text-white flex items-center gap-2">
                            <span class="text-base">📦</span>
                            <span class="font-mono">${escapeHtml(c.name)}</span>
                        </td>
                        <td class="py-3 px-6">
                            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${badgeColor}">
                                <span class="w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-emerald-400 pulse-dot' : isFrozen ? 'bg-cyan-400' : 'bg-slate-500'}"></span>
                                ${c.state}
                            </span>
                        </td>
                        <td class="py-3 px-6">
                            <div class="flex items-center gap-2">
                                <span class="font-mono text-slate-300 text-xs">${c.ip !== '-' ? escapeHtml(c.ip) : '<span class="text-slate-600">--</span>'}</span>
                                ${netModeBadge}
                            </div>
                        </td>
                        <td class="py-3 px-6 font-mono text-slate-300 text-xs">${c.memory !== '-' ? escapeHtml(c.memory) : '<span class="text-slate-600">--</span>'}</td>
                        <td class="py-3 px-6 font-mono text-slate-400 text-xs">${c.cpu !== '-' ? escapeHtml(c.cpu) : '<span class="text-slate-600">--</span>'}</td>
                        <td class="py-3 px-6">
                            <button onclick="toggleAutostart('${escapeHtml(c.name)}')" class="text-xs px-2 py-0.5 rounded border transition ${c.autostart ? 'bg-blue-500/10 text-blue-400 border-blue-500/30' : 'bg-slate-800 text-slate-500 border-slate-700'}" title="Нажмите для переключения">
                                ${c.autostart ? 'Да' : 'Нет'}
                            </button>
                        </td>
                        <td class="py-3 px-6 text-right space-x-1 whitespace-nowrap">
                            ${isRunning ? `
                                <button onclick="openTermModal('${escapeHtml(c.name)}')" class="px-2.5 py-1 bg-emerald-950/50 hover:bg-emerald-900/80 text-emerald-300 rounded text-xs border border-emerald-800/50 transition font-medium" title="Интерактивный веб-терминал">📟 Терминал</button>
                                <button onclick="openExecModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700 transition" title="Быстрый запуск команд">💻 Exec</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-indigo-950/50 hover:bg-indigo-900/80 text-indigo-300 rounded text-xs border border-indigo-800/50 transition" title="Снимки">📸 Снимки</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'restart')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-amber-400 rounded text-xs border border-slate-700 transition" title="Перезагрузить">🔄</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'freeze')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-cyan-400 rounded text-xs border border-slate-700 transition" title="Заморозить">❄️</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'stop')" class="px-2.5 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 rounded text-xs border border-rose-800/40 transition" title="Остановить">⏹️ Стоп</button>
                            ` : isFrozen ? `
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'unfreeze')" class="px-2.5 py-1 bg-cyan-950/40 hover:bg-cyan-900/60 text-cyan-300 rounded text-xs border border-cyan-800/40 transition" title="Разморозить">☀️ Разморозить</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'stop')" class="px-2.5 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 rounded text-xs border border-rose-800/40 transition" title="Остановить">⏹️ Стоп</button>
                            ` : `
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="px-2.5 py-1 bg-indigo-950/50 hover:bg-indigo-900/80 text-indigo-300 rounded text-xs border border-indigo-800/50 transition" title="Снимки">📸 Снимки</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'start')" class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-medium transition" title="Запустить">▶ Старт</button>
                                <button onclick="confirmDestroy('${escapeHtml(c.name)}')" class="px-2.5 py-1 bg-rose-950/30 hover:bg-rose-900/60 text-rose-400 rounded text-xs border border-rose-900/40 transition" title="Удалить">🗑️</button>
                            `}
                        </td>
                    </tr>`;
            }
            tbody.innerHTML = html;
        }

        async function handleAction(name, action) {
            try {
                const res = await fetch(`/api/containers/${name}/action`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ action })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(`${name}: действие '${action}' успешно выполнено`, 'success');
                    refreshData();
                } else {
                    showToast(`${name}: ошибка: ${d.error || d.message}`, 'error');
                }
            } catch (e) {
                showToast(`Ошибка сети: ${e.message}`, 'error');
            }
        }

        async function toggleAutostart(name) {
            await handleAction(name, 'toggle_autostart');
        }

        function confirmDestroy(name) {
            if (confirm(`Вы уверены, что хотите удалить контейнер '${name}' и стереть его диск?`)) {
                handleAction(name, 'destroy');
            }
        }

        function openCreateModal() {
            document.getElementById('create-modal').classList.remove('hidden');
            document.getElementById('new-name').focus();
        }

        function closeCreateModal() {
            document.getElementById('create-modal').classList.add('hidden');
            const progress = document.getElementById('create-progress');
            if (progress) progress.classList.add('hidden');
            const btnSubmit = document.getElementById('btn-create-submit');
            const btnCancel = document.getElementById('btn-create-cancel');
            if (btnSubmit) { btnSubmit.disabled = false; btnSubmit.classList.remove('opacity-50'); btnSubmit.innerText = 'Создать Sandbox'; }
            if (btnCancel) btnCancel.disabled = false;
        }

        async function handleCreate(e) {
            e.preventDefault();
            const btnSubmit = document.getElementById('btn-create-submit');
            const btnCancel = document.getElementById('btn-create-cancel');
            const progress = document.getElementById('create-progress');
            const progressText = document.getElementById('create-progress-text');

            const rawName = document.getElementById('new-name').value.trim();
            const cleanName = rawName.replace(/[^a-zA-Z0-9_\\-]/g, '').toLowerCase();
            if (!cleanName || cleanName.length < 2) {
                showToast('Введите корректное имя контейнера (от 2 символов: латиница, цифры, дефис)', 'error');
                return;
            }

            let distro = document.getElementById('new-distro').value;
            let release = document.getElementById('new-release').value;

            if (distro === 'custom') {
                distro = document.getElementById('custom-distro-input').value.trim();
                release = document.getElementById('custom-release-input').value.trim();
            }

            const netMode = document.querySelector('input[name="net_mode"]:checked').value;

            const payload = {
                name: cleanName,
                distro: distro,
                release: release,
                net_mode: netMode,
                ip: document.getElementById('new-ip').value.trim(),
                autostart: document.getElementById('new-autostart').checked,
                start_now: document.getElementById('new-startnow').checked
            };

            btnSubmit.disabled = true;
            if (btnCancel) btnCancel.disabled = true;
            btnSubmit.classList.add('opacity-50');
            btnSubmit.innerText = 'Развертывание...';
            if (progress) {
                progress.classList.remove('hidden');
                if (progressText) progressText.innerText = `Развертывание '${cleanName}' (${distro} ${release})...`;
            }

            try {
                const res = await fetch('/api/containers/create', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                let d;
                try {
                    d = await res.json();
                } catch (jsonErr) {
                    throw new Error(`Ошибка ответа сервера (HTTP ${res.status})`);
                }
                if (d.success) {
                    showToast(d.message || `Контейнер '${cleanName}' успешно создан!`, d.warning ? 'info' : 'success');
                    closeCreateModal();
                    document.getElementById('create-form').reset();
                    fetchCatalog();
                    refreshData();
                } else {
                    showToast(d.error || 'Ошибка при создании контейнера', 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            } finally {
                btnSubmit.disabled = false;
                if (btnCancel) btnCancel.disabled = false;
                btnSubmit.classList.remove('opacity-50');
                btnSubmit.innerText = 'Создать Sandbox';
                if (progress) progress.classList.add('hidden');
            }
        }

        // --- Terminal Modal (ttyd) ---
        function openTermModal(name) {
            activeTermTarget = name;
            document.getElementById('term-target-name').innerText = name;
            const iframe = document.getElementById('term-iframe');
            iframe.src = `/ttyd/?arg=${encodeURIComponent(name)}`;
            document.getElementById('term-modal').classList.remove('hidden');
        }

        function reloadTermFrame() {
            if (activeTermTarget) {
                const iframe = document.getElementById('term-iframe');
                iframe.src = `/ttyd/?arg=${encodeURIComponent(activeTermTarget)}&t=${Date.now()}`;
            }
        }

        function closeTermModal() {
            document.getElementById('term-modal').classList.add('hidden');
            document.getElementById('term-iframe').src = '';
            refreshData();
        }

        // --- Snapshot Modal (lxc-snapshot) ---
        async function openSnapshotModal(name) {
            activeSnapshotTarget = name;
            document.getElementById('snap-target-name').innerText = name;
            document.getElementById('snapshot-modal').classList.remove('hidden');
            await loadSnapshots(name);
        }

        function closeSnapshotModal() {
            document.getElementById('snapshot-modal').classList.add('hidden');
        }

        async function loadSnapshots(name) {
            const listEl = document.getElementById('snapshots-list');
            listEl.innerHTML = '<div class="text-center py-6 text-slate-500 text-xs">Загрузка снимков...</div>';

            try {
                const res = await fetch(`/api/containers/${name}/snapshots`);
                const d = await res.json();
                const snaps = d.snapshots || [];

                if (snaps.length === 0) {
                    listEl.innerHTML = `
                        <div class="text-center py-8 text-slate-500 border border-dashed border-slate-800 rounded-xl">
                            <div class="text-2xl mb-1">📸</div>
                            <div class="text-xs text-slate-400">Нет созданных снимков для '${escapeHtml(name)}'</div>
                            <div class="text-[11px] text-slate-600 mt-1">Создайте снимок перед экспериментами для мгновенного отката</div>
                        </div>`;
                    return;
                }

                let html = '';
                for (const s of snaps) {
                    html += `
                        <div class="p-3 bg-slate-950 border border-slate-800 rounded-xl flex items-center justify-between text-xs">
                            <div>
                                <div class="font-mono font-bold text-white flex items-center gap-2">
                                    <span>📸</span>
                                    <span>${escapeHtml(s.name)}</span>
                                    <span class="text-[11px] text-slate-500 font-normal">(${escapeHtml(s.date)})</span>
                                </div>
                                <div class="text-slate-400 text-[11px] mt-0.5">${escapeHtml(s.comment || 'Без комментария')}</div>
                            </div>
                            <div class="space-x-1.5 whitespace-nowrap">
                                <button onclick="handleRestoreSnapshot('${escapeHtml(s.name)}')" class="px-2.5 py-1 bg-amber-600/20 hover:bg-amber-600/40 text-amber-300 rounded border border-amber-500/30 transition text-xs font-medium">⏪ Откатиться</button>
                                <button onclick="handleDestroySnapshot('${escapeHtml(s.name)}')" class="px-2 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-400 rounded border border-rose-900/40 transition text-xs">🗑️</button>
                            </div>
                        </div>`;
                }
                listEl.innerHTML = html;
            } catch (e) {
                listEl.innerHTML = `<div class="text-rose-400 text-xs">Ошибка загрузки: ${escapeHtml(e.message)}</div>`;
            }
        }

        async function handleCreateSnapshot(e) {
            e.preventDefault();
            const btn = document.getElementById('btn-snap-create');
            const input = document.getElementById('snap-comment-input');
            const comment = input.value.trim();

            btn.disabled = true;
            btn.innerText = 'Создание...';

            try {
                const res = await fetch(`/api/containers/${activeSnapshotTarget}/snapshots/create`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ comment })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    input.value = '';
                    await loadSnapshots(activeSnapshotTarget);
                } else {
                    showToast(d.error || 'Ошибка при создании снимка', 'error');
                }
            } catch (err) {
                showToast(err.message, 'error');
            } finally {
                btn.disabled = false;
                btn.innerText = '+ Сделать снимок';
            }
        }

        async function handleRestoreSnapshot(snapName) {
            if (!confirm(`Вы действительно хотите откатить контейнер '${activeSnapshotTarget}' к состоянию '${snapName}'? Текущие несохраненные данные будут перезаписаны снимком.`)) {
                return;
            }

            try {
                const res = await fetch(`/api/containers/${activeSnapshotTarget}/snapshots/restore`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ snapshot: snapName })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    refreshData();
                } else {
                    showToast(d.error || 'Ошибка отката', 'error');
                }
            } catch (err) {
                showToast(err.message, 'error');
            }
        }

        async function handleDestroySnapshot(snapName) {
            if (!confirm(`Удалить снимок '${snapName}'?`)) return;

            try {
                const res = await fetch(`/api/containers/${activeSnapshotTarget}/snapshots/destroy`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ snapshot: snapName })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    await loadSnapshots(activeSnapshotTarget);
                } else {
                    showToast(d.error || 'Ошибка удаления', 'error');
                }
            } catch (err) {
                showToast(err.message, 'error');
            }
        }

        // --- Exec Runner Modal ---
        function openExecModal(name) {
            activeExecTarget = name;
            document.getElementById('exec-target-name').innerText = name;
            document.getElementById('exec-modal').classList.remove('hidden');
            document.getElementById('exec-input').focus();
        }

        function closeExecModal() {
            document.getElementById('exec-modal').classList.add('hidden');
        }

        function setExecCmd(cmd) {
            document.getElementById('exec-input').value = cmd;
            document.getElementById('exec-input').focus();
        }

        function clearExecOutput() {
            document.getElementById('exec-output').innerHTML = '<span class="text-slate-500">// Консоль очищена.</span>';
        }

        async function handleExec(e) {
            e.preventDefault();
            const input = document.getElementById('exec-input');
            const cmd = input.value.trim();
            if (!cmd) return;

            const btn = document.getElementById('btn-exec-run');
            btn.disabled = true;
            btn.innerText = 'Выполняю...';

            const outArea = document.getElementById('exec-output');
            const runBlock = document.createElement('div');
            runBlock.className = 'mt-3 pt-2 border-t border-slate-800/80';
            runBlock.innerHTML = `
                <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
                    <span class="text-emerald-400 font-bold">$ ${escapeHtml(cmd)}</span>
                    <span class="text-slate-500">running...</span>
                </div>
                <div class="text-slate-400">...</div>
            `;
            outArea.appendChild(runBlock);
            outArea.scrollTop = outArea.scrollHeight;

            try {
                const res = await fetch(`/api/containers/${activeExecTarget}/exec`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ command: cmd })
                });
                const d = await res.json();
                if (d.success) {
                    const statusColor = d.exit_code === 0 ? 'text-emerald-400' : 'text-rose-400';
                    runBlock.innerHTML = `
                        <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
                            <span class="text-emerald-400 font-bold">$ ${escapeHtml(cmd)}</span>
                            <span class="${statusColor} font-mono">[code ${d.exit_code} | ${d.duration_ms}ms]</span>
                        </div>
                        <pre class="whitespace-pre-wrap font-mono text-slate-200 mt-1">${escapeHtml(d.output || '(нет вывода)')}</pre>
                    `;
                } else {
                    runBlock.innerHTML = `<div class="text-rose-400 font-mono">Ошибка: ${escapeHtml(d.error)}</div>`;
                }
            } catch (err) {
                runBlock.innerHTML = `<div class="text-rose-400 font-mono">Ошибка сети: ${escapeHtml(err.message)}</div>`;
            } finally {
                btn.disabled = false;
                btn.innerText = 'Выполнить';
                outArea.scrollTop = outArea.scrollHeight;
                input.value = '';
                input.focus();
            }
        }

        function escapeHtml(text) {
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function toggleAutoRefresh() {
            const btn = document.getElementById('auto-btn');
            if (autoRefreshInterval) {
                clearInterval(autoRefreshInterval);
                autoRefreshInterval = null;
                btn.innerText = 'Выкл';
                btn.className = 'px-2.5 py-1 text-xs rounded bg-slate-800 text-slate-400 border border-slate-700';
            } else {
                autoRefreshInterval = setInterval(refreshData, 4000);
                btn.innerText = 'Вкл (4с)';
                btn.className = 'px-2.5 py-1 text-xs rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
            }
        }

        function refreshData() {
            const icon = document.getElementById('refresh-icon');
            icon.classList.add('animate-spin');
            Promise.all([fetchMetrics(), fetchContainers()]).finally(() => {
                setTimeout(() => icon.classList.remove('animate-spin'), 400);
            });
        }

        // Initial Load
        fetchCatalog();
        refreshData();
        autoRefreshInterval = setInterval(refreshData, 4000);
    </script>
</body>
</html>
"""

@app.route("/", method="GET")
def index():
    return INDEX_HTML

if __name__ == "__main__":
    import bottle
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, make_server

    class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    class ThreadedWSGIServerAdapter(bottle.ServerAdapter if hasattr(bottle, "ServerAdapter") else object):
        def run(self, handler):
            server = make_server(self.host, self.port, handler, server_class=ThreadingWSGIServer)
            server.serve_forever()

    app.run(host="127.0.0.1", port=8888, server=ThreadedWSGIServerAdapter)
