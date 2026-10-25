#!/usr/bin/env python3
"""
LXC Sandbox Web Manager for Alpine Linux
Ultra-lightweight native WSGI application built with Bottle.
Manages isolated test/sandbox containers alongside bare-metal services.
Features:
  - Live synchronization with official LinuxContainers catalog (images.linuxcontainers.org)
  - Full support for Kali Linux, Debian, Ubuntu, Alpine, Arch, Fedora, Rocky, AlmaLinux, etc.
  - Fully responsive mobile & desktop UI (touch cards on mobile, expansive table on desktop)
  - Home network LAN mode (br0 with DHCP from router) & NAT Sandbox (lxcbr0)
  - Snapshots and Rollback (lxc-snapshot)
  - Interactive Web Terminal (ttyd / xterm.js via WebSocket) with Fullscreen Mode
  - Quick command execution via lxc-attach
"""

import os
import sys
import re
import json
import time
import shutil
import platform
import threading
import subprocess
import urllib.request
from bottle import Bottle, request, response, static_file, abort

app = Bottle()

LXC_DIR = "/var/lib/lxc"
CACHE_DIR = "/var/cache/lxc"
MINIROOTFS_CACHE = os.path.join(LXC_DIR, "cache", "alpine-minirootfs.tar.gz")
CATALOG_CACHE_FILE = os.path.join(LXC_DIR, "cache", "upstream-catalog.json")

UPSTREAM_INDEX_URL = "https://images.linuxcontainers.org/meta/1.0/index-system"
FALLBACK_INDEX_URL = "https://uk.images.linuxcontainers.org/meta/1.0/index-system"

DISTRO_METADATA = {
    "alpine": {"name": "Alpine Linux (Ultra-light, ~270 KB RAM)", "priority": 1, "icon": "⚡"},
    "kali": {"name": "Kali Linux (Penetration Testing & Security)", "priority": 2, "icon": "🐉"},
    "debian": {"name": "Debian GNU/Linux (Стабильный / apt)", "priority": 3, "icon": "🌀"},
    "ubuntu": {"name": "Ubuntu Linux (Canonical LTS)", "priority": 4, "icon": "🐧"},
    "archlinux": {"name": "Arch Linux (Rolling Release)", "priority": 5, "icon": "🏹"},
    "fedora": {"name": "Fedora Linux", "priority": 6, "icon": "🎩"},
    "rockylinux": {"name": "Rocky Linux (RHEL 9 Совместимый)", "priority": 7, "icon": "🏔️"},
    "almalinux": {"name": "AlmaLinux (RHEL 9 Совместимый)", "priority": 8, "icon": "🔴"},
    "voidlinux": {"name": "Void Linux (Независимый / runit)", "priority": 9, "icon": "🪐"},
    "nixos": {"name": "NixOS (Декларативный Linux)", "priority": 10, "icon": "❄️"},
    "openwrt": {"name": "OpenWrt (Сетевой роутер Linux)", "priority": 11, "icon": "📶"},
    "centos": {"name": "CentOS Stream", "priority": 12, "icon": "📦"},
    "amazonlinux": {"name": "Amazon Linux", "priority": 13, "icon": "☁️"},
    "devuan": {"name": "Devuan GNU+Linux (sysvinit)", "priority": 14, "icon": "💎"},
    "mint": {"name": "Linux Mint", "priority": 15, "icon": "🍃"},
    "opensuse": {"name": "openSUSE Linux", "priority": 16, "icon": "🦎"},
    "openeuler": {"name": "openEuler Linux", "priority": 17, "icon": "🌐"},
    "oracle": {"name": "Oracle Linux", "priority": 18, "icon": "🏛️"},
    "slackware": {"name": "Slackware Linux", "priority": 19, "icon": "📜"},
    "busybox": {"name": "BusyBox Tiny Linux", "priority": 20, "icon": "🧰"},
    "netbsd": {"name": "NetBSD", "priority": 21, "icon": "🚩"},
    "alt": {"name": "ALT Linux", "priority": 22, "icon": "🦅"},
    "plamo": {"name": "Plamo Linux", "priority": 23, "icon": "📦"}
}

def run_cmd(cmd, timeout=30):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)

def get_target_arch():
    mach = platform.machine().lower()
    if mach in ["x86_64", "amd64"]:
        return "amd64"
    elif mach in ["aarch64", "arm64"]:
        return "arm64"
    elif "arm" in mach:
        return "armhf"
    elif mach in ["i386", "i686"]:
        return "i386"
    return "amd64"

def get_fallback_catalog():
    return {
        "alpine": {
            "name": "Alpine Linux (Ultra-light, ~270 KB RAM)",
            "icon": "⚡",
            "priority": 1,
            "releases": [
                {"id": "3.24", "name": "3.24 (Current Stable - Быстрый запуск)"},
                {"id": "3.23", "name": "3.23"},
                {"id": "3.22", "name": "3.22"},
                {"id": "3.21", "name": "3.21"},
                {"id": "edge", "name": "edge (Rolling)"}
            ]
        },
        "kali": {
            "name": "Kali Linux (Penetration Testing & Security)",
            "icon": "🐉",
            "priority": 2,
            "releases": [
                {"id": "current", "name": "current (Kali Rolling - Рекомендуется)"}
            ]
        },
        "debian": {
            "name": "Debian GNU/Linux (Стабильный / apt)",
            "icon": "🌀",
            "priority": 3,
            "releases": [
                {"id": "bookworm", "name": "12 Bookworm (Stable)"},
                {"id": "trixie", "name": "13 Trixie (Testing)"},
                {"id": "bullseye", "name": "11 Bullseye (Oldstable)"},
                {"id": "sid", "name": "sid (Unstable)"}
            ]
        },
        "ubuntu": {
            "name": "Ubuntu Linux (Canonical LTS)",
            "icon": "🐧",
            "priority": 4,
            "releases": [
                {"id": "noble", "name": "24.04 LTS Noble Numbat"},
                {"id": "jammy", "name": "22.04 LTS Jammy Jellyfish"},
                {"id": "focal", "name": "20.04 LTS Focal Fossa"}
            ]
        },
        "archlinux": {
            "name": "Arch Linux (Rolling Release)",
            "icon": "🏹",
            "priority": 5,
            "releases": [
                {"id": "current", "name": "current (Daily Rolling)"}
            ]
        },
        "fedora": {
            "name": "Fedora Linux",
            "icon": "🎩",
            "priority": 6,
            "releases": [
                {"id": "41", "name": "41"},
                {"id": "40", "name": "40"}
            ]
        },
        "rockylinux": {
            "name": "Rocky Linux (RHEL 9 Совместимый)",
            "icon": "🏔️",
            "priority": 7,
            "releases": [
                {"id": "9", "name": "9"},
                {"id": "8", "name": "8"}
            ]
        },
        "almalinux": {
            "name": "AlmaLinux (RHEL 9 Совместимый)",
            "icon": "🔴",
            "priority": 8,
            "releases": [
                {"id": "9", "name": "9"},
                {"id": "8", "name": "8"}
            ]
        }
    }

def fetch_upstream_catalog(force=False):
    target_arch = get_target_arch()

    # 1. Check local disk cache (valid for 6 hours unless force is requested)
    if not force and os.path.exists(CATALOG_CACHE_FILE):
        try:
            mtime = os.path.getmtime(CATALOG_CACHE_FILE)
            if (time.time() - mtime) < 21600:
                with open(CATALOG_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if cached.get("catalog") and cached.get("arch") == target_arch:
                        return cached["catalog"], True, cached.get("updated_at", int(mtime))
        except Exception:
            pass

    # 2. Fetch live index from images.linuxcontainers.org
    raw_data = None
    headers = {"User-Agent": "lxc/7.0.0"}
    for url in [UPSTREAM_INDEX_URL, FALLBACK_INDEX_URL]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw_data = resp.read().decode("utf-8", errors="ignore")
                if raw_data and len(raw_data) > 500:
                    break
        except Exception:
            continue

    if not raw_data:
        # Fallback to cached file if exists
        if os.path.exists(CATALOG_CACHE_FILE):
            try:
                with open(CATALOG_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    return cached["catalog"], True, cached.get("updated_at", int(time.time()))
            except Exception:
                pass
        return get_fallback_catalog(), False, int(time.time())

    # 3. Parse semicolon records: distro;release;arch;variant;build_date;path
    distro_releases = {}
    for line in raw_data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) >= 6:
            dist, rel, arch, variant, bdate, path = parts[:6]
            if arch == target_arch and variant in ["default", "cloud"]:
                distro_releases.setdefault(dist.lower(), set()).add(rel)

    # Ensure alpine 3.24 is always in alpine releases
    if "alpine" in distro_releases:
        distro_releases["alpine"].add("3.24")
    else:
        distro_releases["alpine"] = {"3.24", "edge"}

    # Ensure kali has current
    if "kali" in distro_releases:
        distro_releases["kali"].add("current")

    # 4. Build catalog dictionary
    catalog = {}
    for dist, rel_set in distro_releases.items():
        meta = DISTRO_METADATA.get(dist, {
            "name": f"{dist.capitalize()} Linux",
            "priority": 90,
            "icon": "📦"
        })

        # Release sorting key
        def release_sort_key(r):
            # Recommended / current releases first
            if dist == "alpine" and r == "3.24":
                return (0, 0)
            if dist == "kali" and r == "current":
                return (0, 0)
            if r in ["current", "latest", "edge", "rolling"]:
                return (1, 0)
            nums = re.findall(r"\d+", r)
            if nums:
                return (2, -int(nums[0]))
            return (3, r)

        sorted_rels = sorted(list(rel_set), key=release_sort_key)
        rel_objs = []
        for r in sorted_rels:
            friendly_name = r
            if dist == "kali" and r == "current":
                friendly_name = "current (Kali Rolling - Рекомендуется)"
            elif dist == "alpine" and r == "3.24":
                friendly_name = "3.24 (Current Stable - Быстрый запуск)"
            elif dist == "debian" and r == "bookworm":
                friendly_name = "12 Bookworm (Stable)"
            elif dist == "ubuntu" and r == "noble":
                friendly_name = "24.04 LTS Noble"
            elif dist == "archlinux" and r == "current":
                friendly_name = "current (Rolling Daily)"
            rel_objs.append({"id": r, "name": friendly_name})

        catalog[dist] = {
            "name": meta["name"],
            "icon": meta.get("icon", "📦"),
            "priority": meta.get("priority", 90),
            "releases": rel_objs
        }

    # Sort catalog by priority then name
    sorted_catalog = dict(sorted(catalog.items(), key=lambda item: (item[1]["priority"], item[0])))

    # Cache to disk
    try:
        os.makedirs(os.path.dirname(CATALOG_CACHE_FILE), exist_ok=True)
        with open(CATALOG_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "catalog": sorted_catalog,
                "arch": target_arch,
                "updated_at": int(time.time())
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return sorted_catalog, False, int(time.time())

def get_cached_templates():
    cached_images = []
    dl_cache = os.path.join(CACHE_DIR, "download")
    if os.path.exists(dl_cache):
        try:
            for dist in os.listdir(dl_cache):
                dpath = os.path.join(dl_cache, dist)
                if os.path.isdir(dpath):
                    for rel in os.listdir(dpath):
                        rpath = os.path.join(dpath, rel)
                        if os.path.isdir(rpath):
                            cached_images.append(f"{dist}:{rel}")
        except Exception:
            pass
    if os.path.exists(MINIROOTFS_CACHE):
        cached_images.append("alpine:3.24")
    return list(set(cached_images))

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
    catalog, from_cache, updated_at = fetch_upstream_catalog(force=False)
    cached_images = get_cached_templates()
    return {
        "catalog": catalog,
        "cached": cached_images,
        "arch": get_target_arch(),
        "total_distros": len(catalog),
        "from_cache": from_cache,
        "updated_at": updated_at
    }

@app.route("/api/catalog/refresh", method="POST")
def api_catalog_refresh():
    response.content_type = "application/json"
    catalog, _, updated_at = fetch_upstream_catalog(force=True)
    cached_images = get_cached_templates()
    return {
        "success": True,
        "message": f"Каталог успешно синхронизирован с images.linuxcontainers.org! Найдено {len(catalog)} дистрибутивов.",
        "catalog": catalog,
        "cached": cached_images,
        "total_distros": len(catalog),
        "updated_at": updated_at
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
        target_arch = get_target_arch()
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
                cmd = ["lxc-create", "-n", tmp_cname, "-t", "download", "--", "-d", distro, "-r", release, "-a", target_arch]
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

    rootfs_dir = os.path.join(target_dir, "rootfs")
    is_fast_clone = (distro == "alpine" and release in ["3.24", "current", "latest"] and os.path.exists(MINIROOTFS_CACHE))
    target_arch = get_target_arch()

    try:
        if is_fast_clone:
            # 1. Instant local minirootfs cloning (~1s)
            os.makedirs(rootfs_dir, exist_ok=True)
            code, _, err = run_cmd(["tar", "-xzf", MINIROOTFS_CACHE, "-C", rootfs_dir], timeout=30)
            if code != 0:
                shutil.rmtree(target_dir, ignore_errors=True)
                return {"success": False, "error": "Ошибка распаковки rootfs: " + err}

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
            # 2. Official lxc-download template from images.linuxcontainers.org (Kali, Debian, Ubuntu, etc.)
            code, out, err = run_cmd([
                "lxc-create", "-n", name, "-t", "download", "--",
                "-d", distro, "-r", release, "-a", target_arch
            ], timeout=360)
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

            # Pre-configure network inside container if rootfs exists
            if os.path.exists(rootfs_dir):
                etc_dir = os.path.join(rootfs_dir, "etc")
                os.makedirs(etc_dir, exist_ok=True)
                resolv_path = os.path.join(etc_dir, "resolv.conf")
                if not os.path.exists(resolv_path) or os.path.islink(resolv_path):
                    try:
                        if os.path.islink(resolv_path):
                            os.unlink(resolv_path)
                        with open(resolv_path, "w", encoding="utf-8") as f:
                            f.write("nameserver 10.0.3.1\nnameserver 1.1.1.1\nnameserver 8.8.8.8\n")
                    except Exception:
                        pass

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
        return {"success": True, "message": "Снимок успешно создан!"}
    return {"success": False, "error": err or out}

@app.route("/api/containers/<name>/snapshots/restore", method="POST")
def api_snapshots_restore(name):
    response.content_type = "application/json"
    data = request.json or {}
    snap_name = data.get("snapshot", "").strip()
    new_name = data.get("new_name", "").strip()

    if not snap_name:
        return {"success": False, "error": "Snapshot name is required."}

    run_cmd(["lxc-stop", "-n", name, "-k"])

    cmd = ["lxc-snapshot", "-n", name, "-r", snap_name]
    if new_name:
        cmd.extend(["-N", new_name])
    else:
        cmd.extend(["-N", name])

    code, out, err = run_cmd(cmd)
    if code == 0:
        target_to_start = new_name if new_name else name
        run_cmd(["lxc-start", "-n", target_to_start, "-d"])
        return {"success": True, "message": f"Контейнер '{target_to_start}' успешно откатан к снимку '{snap_name}'!"}
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
        return {"success": True, "message": f"Снимок '{snap_name}' успешно удален."}
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
# Modern Fully Responsive Single-Page Dashboard UI
# -----------------------------------------------------------------------------
INDEX_HTML = """<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
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
        body { font-family: 'Inter', sans-serif; -webkit-tap-highlight-color: transparent; }
        .font-mono { font-family: 'JetBrains Mono', monospace; }
        .pulse-dot { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
        /* Custom scrollbars */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.6); }
        ::-webkit-scrollbar-thumb { background: rgba(51, 65, 85, 0.8); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(71, 85, 105, 1); }
        .fullscreen-frame {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            width: 100vw !important;
            height: 100vh !important;
            z-index: 9999 !important;
            max-width: 100vw !important;
            border-radius: 0 !important;
        }
    </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col antialiased selection:bg-brand-500 selection:text-white">

    <!-- Top Navigation Header -->
    <header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-40 px-4 sm:px-6 py-3.5 sm:py-4">
        <div class="max-w-7xl 2xl:max-w-[1700px] mx-auto flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 sm:gap-4">
            <!-- Brand Logo & Title -->
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 to-cyan-500 flex items-center justify-center shadow-lg shadow-brand-500/20 shrink-0">
                    <span class="text-xl">📦</span>
                </div>
                <div class="min-w-0">
                    <h1 class="text-lg sm:text-xl font-bold tracking-tight text-white flex items-center gap-2 flex-wrap">
                        <span>LXC Sandbox Manager</span>
                        <span class="text-[11px] bg-brand-500/20 text-brand-400 border border-brand-500/30 px-2 py-0.5 rounded-full font-mono">Alpine 3.24</span>
                    </h1>
                    <p class="text-[11px] sm:text-xs text-slate-400 truncate">Песочница, снимки и веб-терминалы LinuxContainers</p>
                </div>
            </div>

            <!-- Action Buttons -->
            <div class="flex items-center gap-2 sm:gap-3 self-end sm:self-center w-full sm:w-auto">
                <button onclick="openTemplatesModal()" class="flex-1 sm:flex-none flex items-center justify-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs sm:text-sm px-3 sm:px-3.5 py-2 rounded-lg transition active:scale-95 shadow-sm font-medium">
                    <span>📥</span> <span>Шаблоны ОС</span>
                </button>
                <button onclick="openCreateModal()" class="flex-1 sm:flex-none flex items-center justify-center gap-1.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium text-xs sm:text-sm px-3.5 sm:px-4 py-2 rounded-lg shadow-md shadow-blue-600/20 transition-all active:scale-95">
                    <span>⚡</span> <span>Создать Sandbox</span>
                </button>
                <button onclick="refreshData()" class="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg border border-slate-700 transition shrink-0 active:scale-95" title="Обновить статус">
                    <svg class="w-4 h-4 sm:w-5 sm:h-5" id="refresh-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                </button>
            </div>
        </div>
    </header>

    <!-- Main Content Area -->
    <main class="max-w-7xl 2xl:max-w-[1700px] mx-auto w-full px-4 sm:px-6 py-5 sm:py-6 flex-1 space-y-5 sm:space-y-6">

        <!-- Host Metrics Bar: 2 cols on mobile, 4 cols on desktop -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-4">
            <!-- CPU Load -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1.5">
                    <span>CPU Нагрузка</span>
                    <span class="text-blue-400 font-mono text-[10px] sm:text-xs">LoadAvg</span>
                </div>
                <div id="stat-load" class="text-base sm:text-xl font-bold font-mono text-white truncate">0.00, 0.00, 0.00</div>
                <div class="text-[10px] sm:text-xs text-slate-500 mt-1">1, 5, 15 мин</div>
            </div>

            <!-- Host RAM -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1.5">
                    <span>RAM Хоста</span>
                    <span id="stat-ram-pct" class="text-emerald-400 font-mono text-[10px] sm:text-xs">0%</span>
                </div>
                <div id="stat-ram-text" class="text-base sm:text-xl font-bold font-mono text-white truncate">0 / 0 MB</div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 mt-1.5 overflow-hidden">
                    <div id="stat-ram-bar" class="bg-emerald-500 h-1.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
            </div>

            <!-- LXC Storage -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1.5">
                    <span>Диск LXC</span>
                    <span id="stat-disk-pct" class="text-cyan-400 font-mono text-[10px] sm:text-xs">0%</span>
                </div>
                <div id="stat-disk-text" class="text-base sm:text-xl font-bold font-mono text-white truncate">0 / 0 GB</div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 mt-1.5 overflow-hidden">
                    <div id="stat-disk-bar" class="bg-cyan-500 h-1.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
            </div>

            <!-- Sandbox Counts -->
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1.5">
                    <span>Sandbox Контейнеры</span>
                    <span class="text-purple-400 font-mono text-[10px] sm:text-xs">LXC</span>
                </div>
                <div class="flex items-baseline gap-1.5">
                    <span id="stat-active" class="text-lg sm:text-2xl font-bold text-emerald-400">0</span>
                    <span class="text-[11px] sm:text-xs text-slate-400">работает из</span>
                    <span id="stat-total" class="text-base sm:text-xl font-bold text-white">0</span>
                </div>
                <div class="text-[10px] sm:text-xs text-slate-500 mt-1">Остановлено: <span id="stat-stopped">0</span></div>
            </div>
        </div>

        <!-- Containers Section: Dual View (Mobile Cards vs Desktop Table) -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
            <div class="px-4 sm:px-6 py-3.5 sm:py-4 border-b border-slate-800 flex items-center justify-between gap-2">
                <div>
                    <h2 class="font-bold text-white text-sm sm:text-base flex items-center gap-2">
                        <span>Тестовые контейнеры</span>
                    </h2>
                    <p class="text-[11px] sm:text-xs text-slate-400 hidden sm:block">Поддержка Kali Linux, Debian, Ubuntu, Alpine, мгновенных снимков и веб-терминала</p>
                </div>
                <div class="flex items-center gap-1.5 sm:gap-2">
                    <span class="text-[11px] text-slate-400 hidden sm:inline">Автообновление:</span>
                    <button id="auto-btn" onclick="toggleAutoRefresh()" class="px-2 sm:px-2.5 py-1 text-[11px] rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">Вкл (4с)</button>
                </div>
            </div>

            <!-- 1. Mobile Cards View (< 768px) -->
            <div id="containers-cards" class="block md:hidden divide-y divide-slate-800/80 p-3 space-y-3">
                <div class="py-8 text-center text-slate-500 text-xs">Загрузка контейнеров...</div>
            </div>

            <!-- 2. Desktop Table View (>= 768px) -->
            <div class="hidden md:block overflow-x-auto">
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
        <div class="bg-slate-900/60 border border-slate-800 rounded-xl p-4 sm:p-6">
            <h3 class="text-xs sm:text-sm font-bold text-white mb-2 flex items-center gap-2">
                <span>💡</span> Возможности экосистемы LXC Sandbox
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-slate-400">
                <div class="p-3 bg-slate-950/40 rounded-lg border border-slate-800/80">
                    <div class="font-semibold text-slate-200 mb-1 flex items-center gap-1.5">
                        <span>🌐</span> Сеть LAN (DHCP) или NAT
                    </div>
                    Режим LAN подключает контейнер напрямую к домашнему роутеру (192.168.1.x), а NAT изолирует его в подсети 10.0.3.x.
                </div>
                <div class="p-3 bg-slate-950/40 rounded-lg border border-slate-800/80">
                    <div class="font-semibold text-slate-200 mb-1 flex items-center gap-1.5">
                        <span>📸</span> Мгновенные снимки (Rollback)
                    </div>
                    Фиксируйте снимок системы перед экспериментами и откатывайтесь в один клик через lxc-snapshot.
                </div>
                <div class="p-3 bg-slate-950/40 rounded-lg border border-slate-800/80">
                    <div class="font-semibold text-slate-200 mb-1 flex items-center gap-1.5">
                        <span>📟</span> Полноэкранный Web Shell (ttyd)
                    </div>
                    Интерактивный шелл xterm.js с поддержкой htop, цветов, стрелочек, Tab и разворотом во весь экран.
                </div>
            </div>
        </div>

    </main>

    <!-- Create Container Modal -->
    <div id="create-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-4 sm:p-6 shadow-2xl space-y-4 max-h-[92vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 shrink-0">
                <h3 class="font-bold text-white text-base sm:text-lg flex items-center gap-2">
                    <span>⚡</span> Новый Sandbox Контейнер
                </h3>
                <button onclick="closeCreateModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <form id="create-form" onsubmit="handleCreate(event)" class="space-y-4 text-xs sm:text-sm overflow-y-auto pr-1 flex-1">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Имя контейнера</label>
                    <input type="text" id="new-name" required placeholder="например: kali-pentest, test-sandbox, dev-node" oninput="this.value = this.value.replace(/[^a-zA-Z0-9_\\-]/g, '').toLowerCase()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-xs">
                    <p class="text-[11px] text-slate-500 mt-1">Латиница a-z, цифры и дефис (2-32 символа).</p>
                </div>

                <!-- Cascade Distro & Version Select -->
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                        <div class="flex items-center justify-between mb-1">
                            <label class="block text-xs font-medium text-slate-300">Дистрибутив (ОС)</label>
                            <span id="distro-count-badge" class="text-[10px] text-brand-400 font-mono">23 ОС</span>
                        </div>
                        <select id="new-distro" onchange="updateReleaseDropdown()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs truncate">
                            <!-- Populated dynamically via JS from live catalog -->
                        </select>
                    </div>

                    <div>
                        <label class="block text-xs font-medium text-slate-300 mb-1">Версия / Релиз</label>
                        <select id="new-release" onchange="updateCacheHint()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs font-mono">
                            <!-- Populated dynamically via JS -->
                        </select>
                    </div>
                </div>

                <div id="distro-cache-hint" class="text-xs pt-0.5"></div>

                <!-- Network Mode Radio -->
                <div class="p-3 bg-slate-950/80 rounded-xl border border-slate-800 space-y-2">
                    <label class="block text-xs font-semibold text-white">Режим сети (Network Mode):</label>
                    <label class="flex items-start gap-2.5 cursor-pointer">
                        <input type="radio" name="net_mode" value="lan" checked onchange="toggleNetModeUI()" class="mt-0.5 text-brand-600 bg-slate-900 border-slate-700 focus:ring-0">
                        <div>
                            <span class="text-xs font-medium text-slate-200">Домашняя сеть LAN (br0 - DHCP от роутера)</span>
                            <p class="text-[11px] text-slate-400">Контейнер получит реальный домашний IP (192.168.1.X) и будет виден в сети как отдельный ПК.</p>
                        </div>
                    </label>
                    <label class="flex items-start gap-2.5 cursor-pointer pt-1">
                        <input type="radio" name="net_mode" value="nat" onchange="toggleNetModeUI()" class="mt-0.5 text-brand-600 bg-slate-900 border-slate-700 focus:ring-0">
                        <div>
                            <span class="text-xs font-medium text-slate-200">Изолированная песочница NAT (lxcbr0 - 10.0.3.x)</span>
                            <p class="text-[11px] text-slate-400">Полигон за файрволом, не занимает адреса в домашнем роутере.</p>
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
                        <span class="text-xs text-slate-300">Запустить контейнер сразу после создания</span>
                    </label>
                </div>

                <div id="create-progress" class="hidden p-3 bg-brand-950/40 border border-brand-500/30 rounded-xl space-y-2">
                    <div class="flex items-center gap-2 text-xs text-brand-300 font-medium">
                        <svg class="animate-spin w-4 h-4 text-brand-400 shrink-0" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
                        <span id="create-progress-text">Развертывание контейнера...</span>
                    </div>
                    <p class="text-[11px] text-slate-400">Для кэшированных образов развертывание занимает ~1-2 сек. При первом скачивании из сети (Kali, Ubuntu) может потребоваться 1-2 минуты.</p>
                </div>

                <div class="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-800 shrink-0">
                    <button type="button" id="btn-create-cancel" onclick="closeCreateModal()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Отмена</button>
                    <button type="submit" id="btn-create-submit" class="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white font-medium rounded-lg shadow-md transition text-xs">Создать Sandbox</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Templates Catalog Modal (Dynamic Live Internet Catalog) -->
    <div id="templates-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-4xl w-full p-4 sm:p-6 shadow-2xl space-y-3.5 max-h-[92vh] flex flex-col">
            <!-- Modal Header -->
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 shrink-0">
                <div class="flex items-center gap-2">
                    <span class="text-xl">📥</span>
                    <div>
                        <h3 class="font-bold text-white text-sm sm:text-base">Каталог Шаблонов ОС (LinuxContainers)</h3>
                        <p class="text-[11px] text-slate-400 hidden sm:block">Прямая синхронизация с images.linuxcontainers.org</p>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="refreshUpstreamCatalog()" id="btn-catalog-refresh" class="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs transition active:scale-95" title="Синхронизировать с интернетом">
                        <span id="cat-refresh-icon">🔄</span>
                        <span class="hidden sm:inline">Обновить из сети</span>
                    </button>
                    <button onclick="closeTemplatesModal()" class="text-slate-400 hover:text-white text-2xl leading-none px-1">&times;</button>
                </div>
            </div>

            <!-- Search and Filter Bar -->
            <div class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2 shrink-0">
                <div class="relative flex-1">
                    <input type="text" id="template-search" oninput="filterTemplatesCatalog()" placeholder="🔍 Поиск дистрибутива (например: kali, debian, alpine, arch, rocky)..." class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs font-mono">
                </div>
                <div class="flex items-center gap-2 text-xs text-slate-400 shrink-0 self-end sm:self-center">
                    <span id="templates-stats-count" class="font-mono text-slate-300">23 дистрибутива</span>
                    <span>•</span>
                    <span id="templates-cached-count" class="font-mono text-emerald-400">0 в кэше</span>
                </div>
            </div>

            <!-- Active Background Download Progress Banner -->
            <div id="template-dl-banner" class="hidden p-3 bg-blue-950/40 border border-blue-500/30 rounded-xl space-y-1 shrink-0">
                <div class="flex items-center justify-between text-xs">
                    <span id="template-dl-text" class="text-blue-300 font-medium flex items-center gap-2">
                        <svg class="animate-spin w-4 h-4 text-blue-400 inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
                        Загрузка шаблона...
                    </span>
                    <span class="px-2 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-300 border border-blue-500/30 font-mono">В процессе</span>
                </div>
                <p class="text-[11px] text-slate-400">Фоновая загрузка образа в /var/cache/lxc. Окно можно закрывать, загрузка продолжится.</p>
            </div>

            <!-- Distros List (Scrollable) -->
            <div class="space-y-3 overflow-y-auto flex-1 pr-1" id="templates-list-container">
                <div class="text-center py-8 text-slate-500 text-xs">Загрузка каталога из интернета...</div>
            </div>

            <!-- Modal Footer -->
            <div class="flex items-center justify-between pt-3 border-t border-slate-800 text-xs text-slate-500 shrink-0">
                <span class="truncate">Кэш: <code class="text-slate-400 font-mono text-[11px]">/var/cache/lxc/download</code></span>
                <button type="button" onclick="closeTemplatesModal()" class="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Закрыть</button>
            </div>
        </div>
    </div>

    <!-- Snapshot Management Modal -->
    <div id="snapshot-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-xl w-full p-4 sm:p-6 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 shrink-0">
                <div class="flex items-center gap-2">
                    <span class="text-xl">📸</span>
                    <h3 class="font-bold text-white text-sm sm:text-base">
                        Снимки: <span id="snap-target-name" class="text-brand-400 font-mono">name</span>
                    </h3>
                </div>
                <button onclick="closeSnapshotModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <!-- Create Snapshot Form -->
            <form onsubmit="handleCreateSnapshot(event)" class="flex flex-col sm:flex-row gap-2 shrink-0">
                <input type="text" id="snap-comment-input" placeholder="Комментарий к снимку..." class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs">
                <button type="submit" id="btn-snap-create" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg text-xs whitespace-nowrap transition active:scale-95">+ Сделать снимок</button>
            </form>

            <!-- Snapshots List -->
            <div class="flex-1 overflow-y-auto space-y-2 min-h-[160px] pr-1" id="snapshots-list">
                <div class="text-center py-8 text-slate-500 text-xs">Загрузка снимков...</div>
            </div>
        </div>
    </div>

    <!-- Full Interactive Terminal Modal (ttyd) with Fullscreen Mode -->
    <div id="term-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-2 sm:p-4 hidden">
        <div id="term-container" class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-5xl h-[85vh] shadow-2xl flex flex-col overflow-hidden transition-all duration-300">
            <div class="flex items-center justify-between border-b border-slate-800 px-4 sm:px-5 py-2.5 sm:py-3 bg-slate-950 shrink-0">
                <div class="flex items-center gap-2 sm:gap-3 overflow-hidden">
                    <span class="text-lg shrink-0">📟</span>
                    <h3 class="font-bold text-white text-xs sm:text-sm truncate">
                        Шелл: <span id="term-target-name" class="text-emerald-400 font-mono">name</span>
                    </h3>
                    <span class="text-[10px] bg-slate-800 text-slate-400 px-1.5 py-0.5 rounded font-mono hidden sm:inline">xterm-256color</span>
                </div>
                <div class="flex items-center gap-1.5 sm:gap-2 shrink-0">
                    <button onclick="toggleTermFullscreen()" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 sm:px-2.5 py-1 rounded transition flex items-center gap-1" title="Во весь экран">
                        <span id="fullscreen-icon">⛶</span>
                        <span class="hidden sm:inline">Экран</span>
                    </button>
                    <button onclick="reloadTermFrame()" class="text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 sm:px-2.5 py-1 rounded transition" title="Переподключить">🔄</button>
                    <button onclick="closeTermModal()" class="text-slate-400 hover:text-white text-xl leading-none px-1.5">&times;</button>
                </div>
            </div>
            <div class="flex-1 bg-black relative">
                <iframe id="term-iframe" src="" class="w-full h-full border-none"></iframe>
            </div>
        </div>
    </div>

    <!-- Quick Exec Modal -->
    <div id="exec-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-3xl w-full p-4 sm:p-6 shadow-2xl flex flex-col max-h-[90vh]">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-3 shrink-0">
                <div class="flex items-center gap-2 sm:gap-3">
                    <span class="text-xl">💻</span>
                    <div>
                        <h3 class="font-bold text-white text-sm sm:text-base flex items-center gap-2">
                            Команды: <span id="exec-target-name" class="text-brand-400 font-mono">name</span>
                        </h3>
                        <p class="text-[11px] text-slate-400 hidden sm:block">Выполнение команд через lxc-attach с захватом вывода</p>
                    </div>
                </div>
                <button onclick="closeExecModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <div class="flex flex-wrap items-center gap-1.5 mb-2.5 shrink-0">
                <span class="text-[11px] text-slate-500">Быстрые:</span>
                <button onclick="setExecCmd('uname -a')" class="text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-0.5 rounded border border-slate-700 font-mono">uname -a</button>
                <button onclick="setExecCmd('ip a')" class="text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-0.5 rounded border border-slate-700 font-mono">ip a</button>
                <button onclick="setExecCmd('cat /etc/os-release')" class="text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-0.5 rounded border border-slate-700 font-mono">os-release</button>
                <button onclick="setExecCmd('df -h')" class="text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-0.5 rounded border border-slate-700 font-mono">df -h</button>
                <button onclick="setExecCmd('ping -c 2 1.1.1.1')" class="text-[11px] bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-0.5 rounded border border-slate-700 font-mono">ping 1.1.1.1</button>
            </div>

            <div class="flex-1 bg-slate-950 border border-slate-800 rounded-xl p-3.5 font-mono text-xs overflow-y-auto min-h-[200px] text-slate-200" id="exec-output">
                <span class="text-slate-500">// Введите команду и нажмите Enter для выполнения...</span>
            </div>

            <form id="exec-form" onsubmit="handleExec(event)" class="mt-3 flex gap-2 shrink-0">
                <div class="relative flex-1">
                    <span class="absolute left-3 top-2.5 text-emerald-400 font-mono font-bold">$</span>
                    <input type="text" id="exec-input" placeholder="apt update || apk update" required class="w-full bg-slate-950 border border-slate-700 rounded-lg pl-7 pr-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-xs sm:text-sm">
                </div>
                <button type="submit" id="btn-exec-run" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-medium rounded-lg text-xs sm:text-sm transition shrink-0">Выполнить</button>
                <button type="button" onclick="clearExecOutput()" class="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-400 rounded-lg text-xs sm:text-sm shrink-0">Очистить</button>
            </form>
        </div>
    </div>

    <!-- Notification Toast Container -->
    <div id="toast-container" class="fixed bottom-4 right-4 z-50 space-y-2 max-w-sm w-full pointer-events-none px-2"></div>

    <script>
        let autoRefreshInterval = null;
        let activeExecTarget = '';
        let activeSnapshotTarget = '';
        let activeTermTarget = '';
        let isTermFullscreen = false;
        let catalogData = {};
        let cachedImagesList = [];
        let templatePollTimer = null;

        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            const colors = {
                success: 'bg-emerald-950/95 border-emerald-500/50 text-emerald-200',
                error: 'bg-rose-950/95 border-rose-500/50 text-rose-200',
                info: 'bg-slate-900/95 border-slate-700 text-slate-200'
            };
            toast.className = `border px-4 py-3 rounded-xl shadow-2xl text-xs backdrop-blur flex items-center gap-2 pointer-events-auto transition-all duration-300 transform translate-y-2 opacity-0 ${colors[type] || colors.info}`;
            toast.innerHTML = `<span class="shrink-0">${type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️'}</span> <span class="break-words">${escapeHtml(message)}</span>`;
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
                populateDistroDropdown();
                updateReleaseDropdown();
                updateCatalogStats();
            } catch (e) {
                console.error(e);
            }
        }

        async function refreshUpstreamCatalog() {
            const icon = document.getElementById('cat-refresh-icon');
            const btn = document.getElementById('btn-catalog-refresh');
            if (icon) icon.classList.add('animate-spin');
            if (btn) btn.disabled = true;

            try {
                const res = await fetch('/api/catalog/refresh', { method: 'POST' });
                const d = await res.json();
                if (d.success) {
                    catalogData = d.catalog || {};
                    cachedImagesList = d.cached || [];
                    populateDistroDropdown();
                    updateReleaseDropdown();
                    updateCatalogStats();
                    renderTemplatesCatalog();
                    showToast(d.message, 'success');
                } else {
                    showToast(d.error || 'Ошибка синхронизации', 'error');
                }
            } catch (err) {
                showToast(`Ошибка сети: ${err.message}`, 'error');
            } finally {
                if (icon) icon.classList.remove('animate-spin');
                if (btn) btn.disabled = false;
            }
        }

        function updateCatalogStats() {
            const distCount = Object.keys(catalogData).length;
            const cachedCount = cachedImagesList.length;
            const bBadge = document.getElementById('distro-count-badge');
            if (bBadge) bBadge.innerText = `${distCount} ОС`;
            const statsCount = document.getElementById('templates-stats-count');
            if (statsCount) statsCount.innerText = `${distCount} дистрибутивов`;
            const statsCached = document.getElementById('templates-cached-count');
            if (statsCached) statsCached.innerText = `${cachedCount} в кэше`;
        }

        function populateDistroDropdown() {
            const distroSelect = document.getElementById('new-distro');
            if (!distroSelect) return;
            const currentVal = distroSelect.value;
            let html = '';

            for (const [key, item] of Object.entries(catalogData)) {
                const icon = item.icon || '📦';
                html += `<option value="${key}">${icon} ${item.name}</option>`;
            }
            distroSelect.innerHTML = html;
            if (currentVal && catalogData[currentVal]) {
                distroSelect.value = currentVal;
            } else if (catalogData['alpine']) {
                distroSelect.value = 'alpine';
            }
        }

        function updateReleaseDropdown() {
            const distroSelect = document.getElementById('new-distro');
            const releaseSelect = document.getElementById('new-release');
            if (!distroSelect || !releaseSelect) return;
            const chosen = distroSelect.value;
            const item = catalogData[chosen];

            if (!item || !item.releases || item.releases.length === 0) {
                releaseSelect.innerHTML = '<option value="current">current</option>';
                return;
            }

            let html = '';
            for (const r of item.releases) {
                const isCached = cachedImagesList.includes(`${chosen}:${r.id}`);
                const cacheBadge = isCached ? ' ⚡ [в кэше]' : '';
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

            const isCached = cachedImagesList.includes(`${distro}:${release}`);
            if (isCached) {
                hint.innerHTML = '<span class="text-emerald-400 font-medium flex items-center gap-1">⚡ Шаблон в кэше — создание песочницы займет ~1-2 сек</span>';
            } else {
                hint.innerHTML = `<span class="text-slate-400">📥 Шаблон не в кэше — будет скачан при создании (~1-2 мин). <a href="javascript:void(0)" onclick="closeCreateModal(); openTemplatesModal();" class="text-blue-400 underline hover:text-blue-300">Открыть менеджер шаблонов</a></span>`;
            }
        }

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

        function filterTemplatesCatalog() {
            renderTemplatesCatalog();
        }

        function renderTemplatesCatalog() {
            const container = document.getElementById('templates-list-container');
            if (!container) return;
            const searchInput = document.getElementById('template-search');
            const query = searchInput ? searchInput.value.trim().toLowerCase() : '';

            let html = '';
            let matchedDistros = 0;

            for (const [distKey, dist] of Object.entries(catalogData)) {
                // Filter logic: matches distro key, distro name, or any release name
                const keyMatch = distKey.toLowerCase().includes(query);
                const nameMatch = (dist.name || '').toLowerCase().includes(query);
                const releaseMatch = (dist.releases || []).some(r => (r.id + ' ' + (r.name || '')).toLowerCase().includes(query));

                if (query && !keyMatch && !nameMatch && !releaseMatch) {
                    continue;
                }

                matchedDistros++;
                const icon = dist.icon || '📦';
                html += `
                <div class="p-3 sm:p-3.5 bg-slate-950/70 border border-slate-800 rounded-xl space-y-2">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <span class="text-lg">${icon}</span>
                            <span class="font-semibold text-white text-xs sm:text-sm">${escapeHtml(dist.name)}</span>
                        </div>
                        <span class="text-[11px] text-slate-500 font-mono px-2 py-0.5 bg-slate-900 rounded">${escapeHtml(distKey)}</span>
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 pt-1">`;

                for (const r of (dist.releases || [])) {
                    const tag = `${distKey}:${r.id}`;
                    const isCached = cachedImagesList.includes(tag);
                    html += `
                        <div class="flex items-center justify-between p-2 bg-slate-900 border border-slate-800/80 rounded-lg text-xs">
                            <div class="flex items-center gap-1.5 overflow-hidden min-w-0">
                                <span class="font-medium text-slate-300 truncate">${escapeHtml(r.name || r.id)}</span>
                                ${isCached ? '<span class="text-[9px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-1 py-0.5 rounded font-mono shrink-0">⚡ В кэше</span>' : ''}
                            </div>
                            <div class="shrink-0 ml-1.5">
                                ${isCached ? `
                                    <button onclick="deleteTemplate('${escapeHtml(distKey)}', '${escapeHtml(r.id)}')" class="px-2 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 rounded border border-rose-800/40 text-[11px] transition active:scale-95" title="Удалить из кэша">🗑️</button>
                                ` : `
                                    <button onclick="downloadTemplate('${escapeHtml(distKey)}', '${escapeHtml(r.id)}')" class="px-2 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-[11px] font-medium transition active:scale-95 flex items-center gap-1 shadow-sm">📥 Скачать</button>
                                `}
                            </div>
                        </div>`;
                }
                html += `</div></div>`;
            }

            if (matchedDistros === 0) {
                html = `
                    <div class="text-center py-12 text-slate-500">
                        <div class="text-3xl mb-2">🔍</div>
                        <div class="text-slate-300 font-medium">Ничего не найдено по запросу "${escapeHtml(query)}"</div>
                        <div class="text-xs text-slate-500 mt-1">Попробуйте ввести 'kali', 'debian', 'alpine' или 'ubuntu'</div>
                    </div>`;
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
                    if (text) text.innerHTML = `<svg class="animate-spin w-4 h-4 text-blue-400 inline mr-2 shrink-0" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>${escapeHtml(d.message)}`;
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
            const cardsContainer = document.getElementById('containers-cards');

            if (!containers || containers.length === 0) {
                const emptyHtml = `
                    <div class="py-10 text-center text-slate-500">
                        <div class="text-3xl mb-2">📦</div>
                        <div class="text-slate-300 font-medium">Нет созданных Sandbox контейнеров</div>
                        <div class="text-xs text-slate-500 mt-1">Нажмите «Создать Sandbox» для запуска тестового окружения</div>
                    </div>`;
                if (tbody) tbody.innerHTML = `<tr><td colspan="7">${emptyHtml}</td></tr>`;
                if (cardsContainer) cardsContainer.innerHTML = emptyHtml;
                return;
            }

            let tableHtml = '';
            let cardsHtml = '';

            for (const c of containers) {
                const isRunning = c.state === 'RUNNING';
                const isFrozen = c.state === 'FROZEN';
                const badgeColor = isRunning ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 
                                   isFrozen ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' :
                                   'bg-slate-800 text-slate-400 border-slate-700';

                const netModeBadge = c.net_mode === 'lan' ? 
                    '<span class="text-[10px] bg-blue-500/20 text-blue-400 border border-blue-500/30 px-1.5 py-0.5 rounded font-mono">LAN (br0)</span>' :
                    '<span class="text-[10px] bg-purple-500/20 text-purple-400 border border-purple-500/30 px-1.5 py-0.5 rounded font-mono">NAT (lxcbr0)</span>';

                // Desktop Table Row
                tableHtml += `
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
                                <button onclick="openTermModal('${escapeHtml(c.name)}')" class="px-2.5 py-1 bg-emerald-950/50 hover:bg-emerald-900/80 text-emerald-300 rounded text-xs border border-emerald-800/50 transition font-medium" title="Интерактивный шелл">📟 Терминал</button>
                                <button onclick="openExecModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700 transition" title="Быстрый запуск команд">💻 Exec</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-indigo-950/50 hover:bg-indigo-900/80 text-indigo-300 rounded text-xs border border-indigo-800/50 transition" title="Снимки">📸 Снимки</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'restart')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-amber-400 rounded text-xs border border-slate-700 transition" title="Перезагрузить">🔄</button>
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

                // Mobile Card View
                cardsHtml += `
                    <div class="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5 space-y-3 shadow-sm">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-2 overflow-hidden">
                                <span class="text-base">📦</span>
                                <span class="font-bold font-mono text-white text-sm truncate">${escapeHtml(c.name)}</span>
                            </div>
                            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border ${badgeColor} shrink-0">
                                <span class="w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-emerald-400 pulse-dot' : 'bg-slate-500'}"></span>
                                ${c.state}
                            </span>
                        </div>

                        <!-- Card Details Grid -->
                        <div class="grid grid-cols-2 gap-2 text-xs bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/80">
                            <div>
                                <span class="text-slate-500 text-[10px] block">IP АДРЕС</span>
                                <span class="font-mono text-slate-200 text-xs">${c.ip !== '-' ? escapeHtml(c.ip) : '<span class="text-slate-600">--</span>'}</span>
                            </div>
                            <div>
                                <span class="text-slate-500 text-[10px] block">РЕЖИМ СЕТИ</span>
                                <div>${netModeBadge}</div>
                            </div>
                            <div>
                                <span class="text-slate-500 text-[10px] block">ПАМЯТЬ RAM</span>
                                <span class="font-mono text-slate-200 text-xs">${c.memory !== '-' ? escapeHtml(c.memory) : '<span class="text-slate-600">--</span>'}</span>
                            </div>
                            <div>
                                <span class="text-slate-500 text-[10px] block">АВТОСТАРТ</span>
                                <button onclick="toggleAutostart('${escapeHtml(c.name)}')" class="text-[11px] px-2 py-0.5 rounded border transition ${c.autostart ? 'bg-blue-500/10 text-blue-400 border-blue-500/30' : 'bg-slate-800 text-slate-500 border-slate-700'}">
                                    ${c.autostart ? 'Включен' : 'Выключен'}
                                </button>
                            </div>
                        </div>

                        <!-- Card Action Buttons Grid -->
                        <div class="grid grid-cols-2 sm:grid-cols-3 gap-1.5 pt-1">
                            ${isRunning ? `
                                <button onclick="openTermModal('${escapeHtml(c.name)}')" class="flex items-center justify-center gap-1 py-2 bg-emerald-950/60 hover:bg-emerald-900/80 text-emerald-300 rounded-lg text-xs border border-emerald-800/50 font-medium active:scale-95">📟 Терминал</button>
                                <button onclick="openExecModal('${escapeHtml(c.name)}')" class="flex items-center justify-center gap-1 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs border border-slate-700 active:scale-95">💻 Exec</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="flex items-center justify-center gap-1 py-2 bg-indigo-950/60 hover:bg-indigo-900/80 text-indigo-300 rounded-lg text-xs border border-indigo-800/50 active:scale-95">📸 Снимки</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'restart')" class="flex items-center justify-center gap-1 py-2 bg-slate-800 hover:bg-slate-700 text-amber-400 rounded-lg text-xs border border-slate-700 active:scale-95">🔄 Рестарт</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'stop')" class="flex items-center justify-center gap-1 py-2 bg-rose-950/50 hover:bg-rose-900/70 text-rose-300 rounded-lg text-xs border border-rose-800/40 active:scale-95">⏹️ Стоп</button>
                            ` : `
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'start')" class="flex items-center justify-center gap-1 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium active:scale-95">▶ Старт</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="flex items-center justify-center gap-1 py-2 bg-indigo-950/60 hover:bg-indigo-900/80 text-indigo-300 rounded-lg text-xs border border-indigo-800/50 active:scale-95">📸 Снимки</button>
                                <button onclick="confirmDestroy('${escapeHtml(c.name)}')" class="flex items-center justify-center gap-1 py-2 bg-rose-950/40 hover:bg-rose-900/60 text-rose-400 rounded-lg text-xs border border-rose-900/40 active:scale-95">🗑️ Удалить</button>
                            `}
                        </div>
                    </div>`;
            }

            if (tbody) tbody.innerHTML = tableHtml;
            if (cardsContainer) cardsContainer.innerHTML = cardsHtml;
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
                    showToast(`${name}: действие '${action}' выполнено`, 'success');
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

            const distro = document.getElementById('new-distro').value;
            const release = document.getElementById('new-release').value;
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

        // --- Terminal Modal (ttyd) with Fullscreen Mode ---
        function openTermModal(name) {
            activeTermTarget = name;
            document.getElementById('term-target-name').innerText = name;
            const iframe = document.getElementById('term-iframe');
            iframe.src = `/ttyd/?arg=${encodeURIComponent(name)}`;
            document.getElementById('term-modal').classList.remove('hidden');
        }

        function toggleTermFullscreen() {
            const container = document.getElementById('term-container');
            const icon = document.getElementById('fullscreen-icon');
            isTermFullscreen = !isTermFullscreen;
            if (isTermFullscreen) {
                container.classList.add('fullscreen-frame');
                if (icon) icon.innerText = '🗗';
            } else {
                container.classList.remove('fullscreen-frame');
                if (icon) icon.innerText = '⛶';
            }
        }

        function reloadTermFrame() {
            if (activeTermTarget) {
                const iframe = document.getElementById('term-iframe');
                iframe.src = `/ttyd/?arg=${encodeURIComponent(activeTermTarget)}&t=${Date.now()}`;
            }
        }

        function closeTermModal() {
            if (isTermFullscreen) toggleTermFullscreen();
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
                        <div class="p-3 bg-slate-950 border border-slate-800 rounded-xl flex items-center justify-between text-xs gap-2">
                            <div class="overflow-hidden">
                                <div class="font-mono font-bold text-white flex items-center gap-1.5 truncate">
                                    <span>📸</span>
                                    <span>${escapeHtml(s.name)}</span>
                                    <span class="text-[11px] text-slate-500 font-normal">(${escapeHtml(s.date)})</span>
                                </div>
                                <div class="text-slate-400 text-[11px] mt-0.5 truncate">${escapeHtml(s.comment || 'Без комментария')}</div>
                            </div>
                            <div class="space-x-1 whitespace-nowrap shrink-0">
                                <button onclick="handleRestoreSnapshot('${escapeHtml(s.name)}')" class="px-2.5 py-1 bg-amber-600/20 hover:bg-amber-600/40 text-amber-300 rounded border border-amber-500/30 transition text-xs font-medium active:scale-95">⏪ Откат</button>
                                <button onclick="handleDestroySnapshot('${escapeHtml(s.name)}')" class="px-2 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-400 rounded border border-rose-900/40 transition text-xs active:scale-95">🗑️</button>
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
            if (!confirm(`Откатить контейнер '${activeSnapshotTarget}' к состоянию '${snapName}'? Несохраненные данные будут перезаписаны.`)) {
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
                btn.className = 'px-2 sm:px-2.5 py-1 text-[11px] rounded bg-slate-800 text-slate-400 border border-slate-700';
            } else {
                autoRefreshInterval = setInterval(refreshData, 4000);
                btn.innerText = 'Вкл (4с)';
                btn.className = 'px-2 sm:px-2.5 py-1 text-[11px] rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
            }
        }

        function refreshData() {
            const icon = document.getElementById('refresh-icon');
            if (icon) icon.classList.add('animate-spin');
            Promise.all([fetchMetrics(), fetchContainers()]).finally(() => {
                if (icon) setTimeout(() => icon.classList.remove('animate-spin'), 400);
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
