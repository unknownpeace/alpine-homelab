#!/usr/bin/env python3
"""
LXC Sandbox Web Manager 2.0 for Alpine Linux
Ultra-lightweight native WSGI application built with Bottle.
Enterprise-grade container orchestration matching LXD/Incus & Proxmox VE standards:
  - Live synchronization with official LinuxContainers catalog (23+ distros including Kali)
  - Full mobile & desktop responsive UI (Container Cards on mobile, rich table on desktop)
  - cgroups v2 dynamic resource limits (RAM & CPU limits applied on-the-fly)
  - 1-Click Caddy Ingress (auto reverse-proxy publishing with HTTPS and PKI SSL)
  - Inline Rootfs File Explorer & Web Code Editor with syntax styling
  - 1-Click Container Cloning (lxc-copy) and Golden Image templating
  - Container Backup & Restore (tar.gz export & import)
  - Pre-seed Cloud-Init (SSH public key injection & post-install scripts)
  - Live SVG Telemetry Graphs (CPU %, RAM MB, Net RX/TX)
  - Interactive Web Shell (ttyd / xterm.js) with Fullscreen Mode
  - Snapshots and Rollback (lxc-snapshot)
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
CADDY_CONF_DIR = "/etc/caddy/conf.d"

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

# CPU history for telemetry delta calculations: {name: (timestamp, usage_usec)}
CPU_METRIC_HISTORY = {}
METRIC_LOCK = threading.Lock()

def run_cmd(cmd, timeout=30):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)

def get_base_domain():
    domain = os.environ.get("BASE_DOMAIN", "")
    if domain:
        return domain
    caddyfile = "/etc/caddy/Caddyfile"
    if os.path.exists(caddyfile):
        try:
            with open(caddyfile, "r", encoding="utf-8") as f:
                content = f.read()
                m = re.search(r"homelab\.([a-zA-Z0-9.\-]+)", content)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
    return "lan"

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

def reload_caddy():
    caddyfile = "/etc/caddy/Caddyfile"
    if os.path.exists(caddyfile):
        code, out, err = run_cmd(["caddy", "reload", "--config", caddyfile])
        if code != 0:
            run_cmd(["rc-service", "caddy", "reload"])
        return code == 0
    return False

# -----------------------------------------------------------------------------
# Catalog Synchronization
# -----------------------------------------------------------------------------

def fetch_upstream_catalog(force=False):
    target_arch = get_target_arch()

    # 1. Check local disk cache (valid for 6 hours)
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

    # 2. Fetch live index
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
        if os.path.exists(CATALOG_CACHE_FILE):
            try:
                with open(CATALOG_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    return cached["catalog"], True, cached.get("updated_at", int(time.time()))
            except Exception:
                pass
        return get_fallback_catalog(), False, int(time.time())

    # 3. Parse records
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

    if "alpine" in distro_releases:
        distro_releases["alpine"].add("3.24")
    else:
        distro_releases["alpine"] = {"3.24", "edge"}

    if "kali" in distro_releases:
        distro_releases["kali"].add("current")

    catalog = {}
    for dist, rel_set in distro_releases.items():
        meta = DISTRO_METADATA.get(dist, {
            "name": f"{dist.capitalize()} Linux",
            "priority": 90,
            "icon": "📦"
        })

        def release_sort_key(r):
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

    sorted_catalog = dict(sorted(catalog.items(), key=lambda item: (item[1]["priority"], item[0])))

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

def get_fallback_catalog():
    return {
        "alpine": {
            "name": "Alpine Linux (Ultra-light, ~270 KB RAM)",
            "icon": "⚡",
            "priority": 1,
            "releases": [
                {"id": "3.24", "name": "3.24 (Current Stable - Быстрый запуск)"},
                {"id": "3.23", "name": "3.23"},
                {"id": "edge", "name": "edge"}
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
                {"id": "trixie", "name": "13 Trixie (Testing)"}
            ]
        },
        "ubuntu": {
            "name": "Ubuntu Linux (Canonical LTS)",
            "icon": "🐧",
            "priority": 4,
            "releases": [
                {"id": "noble", "name": "24.04 LTS Noble"},
                {"id": "jammy", "name": "22.04 LTS Jammy"}
            ]
        }
    }

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

# -----------------------------------------------------------------------------
# Container Details and Cgroup v2 Metrics
# -----------------------------------------------------------------------------

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
        "net_mode": "nat",
        "mem_limit": "max",
        "cpu_limit": "max",
        "routes_count": 0
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
                # Parse limits from config
                m_mem = re.search(r"^\s*lxc\.cgroup2\.memory\.max\s*=\s*([^\s]+)", content, re.M)
                if m_mem:
                    data["mem_limit"] = m_mem.group(1)
                m_cpu = re.search(r"^\s*lxc\.cgroup2\.cpu\.max\s*=\s*([^\s]+)", content, re.M)
                if m_cpu:
                    data["cpu_limit"] = m_cpu.group(1)
        except Exception:
            pass

    # Read live cgroups v2 limits if running
    cg_path = f"/sys/fs/cgroup/lxc.payload.{name}"
    if os.path.exists(cg_path):
        try:
            mem_max_file = os.path.join(cg_path, "memory.max")
            if os.path.exists(mem_max_file):
                with open(mem_max_file, "r") as f:
                    val = f.read().strip()
                    if val != "max":
                        try:
                            mb = int(val) // (1024 * 1024)
                            data["mem_limit"] = f"{mb} MB"
                        except ValueError:
                            data["mem_limit"] = val
                    else:
                        data["mem_limit"] = "max"
        except Exception:
            pass

    # Count published Caddy routes
    if os.path.exists(CADDY_CONF_DIR):
        try:
            prefix = f"lxc-route-{name}-"
            routes = [f for f in os.listdir(CADDY_CONF_DIR) if f.startswith(prefix) and f.endswith(".conf")]
            data["routes_count"] = len(routes)
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
        "suggested_ip": suggested_ip,
        "base_domain": get_base_domain()
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
        "message": f"Каталог синхронизирован с images.linuxcontainers.org! Найдено {len(catalog)} дистрибутивов.",
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

# -----------------------------------------------------------------------------
# Phase 1: Creation with Pre-seed, Limits & Cloning
# -----------------------------------------------------------------------------

def ensure_container_dns(name, net_mode=None):
    """
    Ensure the container has working DNS resolvers in /etc/resolv.conf.
    For NAT mode containers (lxcbr0), upstream distro templates (Kali, Ubuntu, Debian, etc.)
    frequently contain dead systemd-resolved stubs (127.0.0.53) which do not resolve in LXC on Alpine.
    We unconditionally replace or update them with the host gateway (10.0.3.1) and upstream fallbacks (1.1.1.1, 8.8.8.8).
    """
    try:
        cont_dir = os.path.join(LXC_DIR, name)
        cfg_path = os.path.join(cont_dir, "config")
        rootfs_dir = os.path.join(cont_dir, "rootfs")
        if not os.path.exists(rootfs_dir):
            return

        if net_mode is None and os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg_txt = f.read()
            if "lxcbr0" in cfg_txt:
                net_mode = "nat"
            elif "br0" in cfg_txt:
                net_mode = "lan"

        if net_mode == "nat":
            etc_dir = os.path.join(rootfs_dir, "etc")
            os.makedirs(etc_dir, exist_ok=True)
            resolv_path = os.path.join(etc_dir, "resolv.conf")

            needs_update = True
            if os.path.exists(resolv_path) and not os.path.islink(resolv_path):
                try:
                    with open(resolv_path, "r", encoding="utf-8") as rf:
                        content = rf.read()
                    if ("10.0.3.1" in content or "1.1.1.1" in content) and "127.0.0.53" not in content:
                        needs_update = False
                except Exception:
                    needs_update = True

            if needs_update:
                if os.path.islink(resolv_path) or os.path.exists(resolv_path):
                    try:
                        os.unlink(resolv_path)
                    except Exception:
                        pass
                with open(resolv_path, "w", encoding="utf-8") as f:
                    f.write("nameserver 10.0.3.1\nnameserver 1.1.1.1\nnameserver 8.8.8.8\n")
    except Exception as e:
        logger.warning(f"Failed to ensure DNS for container {name}: {e}")

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

    # Phase 1: cgroup limits & pre-seed
    memory_mb = int(data.get("memory_mb", 0) or 0)
    cpu_quota = int(data.get("cpu_quota", 0) or 0)
    ssh_key = data.get("ssh_key", "").strip()
    init_script = data.get("init_script", "").strip()

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

            # Resource limits config directives
            cgroup_cfg = ""
            if memory_mb > 0:
                cgroup_cfg += f"lxc.cgroup2.memory.max = {memory_mb}M\n"
            if cpu_quota > 0:
                quota = cpu_quota * 1000
                cgroup_cfg += f"lxc.cgroup2.cpu.max = {quota} 100000\n"

            config_content = f"""lxc.uts.name = {name}
lxc.rootfs.path = dir:{rootfs_dir}
{net_cfg}
{cgroup_cfg}lxc.tty.max = 4
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
                ensure_container_dns(clean_name, net_mode)

            if os.path.exists("/etc/apk/repositories"):
                apk_dir = os.path.join(etc_dir, "apk")
                os.makedirs(apk_dir, exist_ok=True)
                shutil.copy2("/etc/apk/repositories", os.path.join(apk_dir, "repositories"))

        else:
            # Official lxc-download template
            code, out, err = run_cmd([
                "lxc-create", "-n", name, "-t", "download", "--",
                "-d", distro, "-r", release, "-a", target_arch
            ], timeout=360)
            if code != 0:
                shutil.rmtree(target_dir, ignore_errors=True)
                return {"success": False, "error": f"Ошибка создания контейнера (lxc-download): {err or out}"}

            cfg_path = os.path.join(target_dir, "config")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                new_lines = []
                for line in lines:
                    if not line.strip().startswith("lxc.net.") and not line.strip().startswith("lxc.start.auto") and not line.strip().startswith("lxc.cgroup2."):
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

                if memory_mb > 0:
                    new_lines.append(f"lxc.cgroup2.memory.max = {memory_mb}M\n")
                if cpu_quota > 0:
                    quota = cpu_quota * 1000
                    new_lines.append(f"lxc.cgroup2.cpu.max = {quota} 100000\n")

                new_lines.append(f"lxc.start.auto = {'1' if autostart else '0'}\n")
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)

            # Ensure container DNS configuration
            ensure_container_dns(clean_name, net_mode)

        # Pre-seed: Inject SSH Public Key
        if ssh_key and os.path.exists(rootfs_dir):
            try:
                ssh_dir = os.path.join(rootfs_dir, "root", ".ssh")
                os.makedirs(ssh_dir, exist_ok=True)
                os.chmod(ssh_dir, 0o700)
                ak_path = os.path.join(ssh_dir, "authorized_keys")
                with open(ak_path, "a", encoding="utf-8") as f:
                    f.write("\n" + ssh_key.strip() + "\n")
                os.chmod(ak_path, 0o600)
            except Exception:
                pass

        # Pre-seed: Post-install Init Script
        if init_script and os.path.exists(rootfs_dir):
            try:
                init_sh = os.path.join(rootfs_dir, "root", "init-setup.sh")
                with open(init_sh, "w", encoding="utf-8") as f:
                    f.write("#!/bin/sh\n" + init_script.strip() + "\n")
                os.chmod(init_sh, 0o755)
            except Exception:
                pass

        if start_now:
            s_code, _, s_err = run_cmd(["lxc-start", "-n", name, "-d"])
            if s_code != 0:
                return {"success": True, "message": f"Контейнер '{name}' создан, но не смог запуститься: {s_err}", "warning": s_err}

            # Asynchronously execute init-setup.sh if present
            if init_script:
                def _run_init():
                    time.sleep(2.5)
                    run_cmd(["lxc-attach", "-n", name, "--", "/root/init-setup.sh"], timeout=180)
                threading.Thread(target=_run_init, daemon=True).start()

        return {"success": True, "message": f"Контейнер '{name}' ({distro} {release}) успешно создан!"}

    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"success": False, "error": str(e)}

@app.route("/api/containers/<name>/limits", method="GET")
def api_get_limits(name):
    response.content_type = "application/json"
    target_dir = os.path.join(LXC_DIR, name)
    if not os.path.exists(target_dir):
        return {"success": False, "error": "Container does not exist."}

    cfg_file = os.path.join(target_dir, "config")
    mem_mb = 0
    cpu_quota = 0

    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            content = f.read()
            m_mem = re.search(r"^\s*lxc\.cgroup2\.memory\.max\s*=\s*(\d+)M", content, re.M)
            if m_mem:
                mem_mb = int(m_mem.group(1))
            m_cpu = re.search(r"^\s*lxc\.cgroup2\.cpu\.max\s*=\s*(\d+)", content, re.M)
            if m_cpu:
                cpu_quota = int(m_cpu.group(1)) // 1000

    # Live cgroup status
    cg_path = f"/sys/fs/cgroup/lxc.payload.{name}"
    current_mem_mb = 0
    if os.path.exists(cg_path):
        cur_file = os.path.join(cg_path, "memory.current")
        if os.path.exists(cur_file):
            try:
                with open(cur_file, "r") as f:
                    current_mem_mb = round(int(f.read().strip()) / (1024 * 1024), 1)
            except Exception:
                pass

    return {
        "success": True,
        "name": name,
        "memory_mb": mem_mb,
        "cpu_quota": cpu_quota,
        "current_mem_mb": current_mem_mb
    }

@app.route("/api/containers/<name>/limits", method="POST")
def api_set_limits(name):
    response.content_type = "application/json"
    data = request.json or {}
    target_dir = os.path.join(LXC_DIR, name)
    if not os.path.exists(target_dir):
        return {"success": False, "error": "Container does not exist."}

    try:
        mem_mb = int(data.get("memory_mb", 0) or 0)
        cpu_quota = int(data.get("cpu_quota", 0) or 0)
    except ValueError:
        return {"success": False, "error": "Invalid limit values."}

    cfg_file = os.path.join(target_dir, "config")
    if os.path.exists(cfg_file):
        with open(cfg_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            if not line.strip().startswith("lxc.cgroup2.memory.max") and not line.strip().startswith("lxc.cgroup2.cpu.max"):
                new_lines.append(line)

        if mem_mb > 0:
            new_lines.append(f"lxc.cgroup2.memory.max = {mem_mb}M\n")
        if cpu_quota > 0:
            quota = cpu_quota * 1000
            new_lines.append(f"lxc.cgroup2.cpu.max = {quota} 100000\n")

        with open(cfg_file, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    # Apply on-the-fly via lxc-cgroup if running
    c_info = parse_container_info(name)
    if c_info.get("state") == "RUNNING":
        mem_arg = f"{mem_mb}M" if mem_mb > 0 else "max"
        run_cmd(["lxc-cgroup", "-n", name, "memory.max", mem_arg])
        cpu_arg = f"{cpu_quota * 1000} 100000" if cpu_quota > 0 else "max 100000"
        run_cmd(["lxc-cgroup", "-n", name, "cpu.max", cpu_arg])

    return {"success": True, "message": f"Лимиты для '{name}' успешно обновлены (RAM: {mem_mb or 'без лимита'} MB, CPU: {cpu_quota or 'без лимита'}%)!"}

@app.route("/api/containers/<name>/clone", method="POST")
def api_clone(name):
    response.content_type = "application/json"
    data = request.json or {}
    new_name = data.get("new_name", "").strip()
    clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "-", new_name).strip("-").lower()
    start_now = bool(data.get("start_now", True))

    if not clean_name or len(clean_name) < 2 or len(clean_name) > 32:
        return {"success": False, "error": "Имя нового контейнера должно содержать от 2 до 32 символов."}

    src_dir = os.path.join(LXC_DIR, name)
    dest_dir = os.path.join(LXC_DIR, clean_name)
    if not os.path.exists(src_dir):
        return {"success": False, "error": f"Исходный контейнер '{name}' не найден."}
    if os.path.exists(dest_dir):
        return {"success": False, "error": f"Контейнер с именем '{clean_name}' уже существует."}

    c_info = parse_container_info(name)
    was_running = (c_info.get("state") == "RUNNING")
    if was_running:
        run_cmd(["lxc-stop", "-n", name, "-k"])

    try:
        code, out, err = run_cmd(["lxc-copy", "-n", name, "-N", clean_name], timeout=300)
        if code != 0:
            return {"success": False, "error": f"Ошибка клонирования: {err or out}"}

        # Update IP in clone config if NAT
        cfg_path = os.path.join(dest_dir, "config")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = f.read()
            # If NAT with static IP, assign next available IP
            if "10.0.3." in content:
                metrics = get_host_metrics()
                new_ip = metrics.get("suggested_ip", "10.0.3.50")
                content = re.sub(r"lxc\.net\.0\.ipv4\.address\s*=\s*10\.0\.3\.\d+", f"lxc.net.0.ipv4.address = {new_ip}", content)
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.write(content)

        ensure_container_dns(clean_name)

        if start_now:
            run_cmd(["lxc-start", "-n", clean_name, "-d"])

        return {"success": True, "message": f"Контейнер '{clean_name}' успешно клонирован из '{name}'!"}
    finally:
        if was_running:
            run_cmd(["lxc-start", "-n", name, "-d"])

# -----------------------------------------------------------------------------
# Phase 2: 1-Click Caddy Ingress & Port Publishing
# -----------------------------------------------------------------------------

@app.route("/api/containers/<name>/routes", method="GET")
def api_get_routes(name):
    response.content_type = "application/json"
    os.makedirs(CADDY_CONF_DIR, exist_ok=True)
    base_domain = get_base_domain()
    prefix = f"lxc-route-{name}-"
    routes = []

    for fname in os.listdir(CADDY_CONF_DIR):
        if fname.startswith(prefix) and fname.endswith(".conf"):
            subdomain = fname[len(prefix):-5]
            fpath = os.path.join(CADDY_CONF_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                    port_m = re.search(r"reverse_proxy\s+[0-9.]+:\s*(\d+)", content)
                    port = port_m.group(1) if port_m else "?"
                    has_auth = "import homelab_auth" in content
                    routes.append({
                        "subdomain": subdomain,
                        "full_domain": f"{subdomain}.{base_domain}",
                        "port": port,
                        "auth": has_auth
                    })
            except Exception:
                pass

    return {
        "success": True,
        "name": name,
        "base_domain": base_domain,
        "routes": sorted(routes, key=lambda x: x["subdomain"])
    }

@app.route("/api/containers/<name>/routes", method="POST")
def api_create_route(name):
    response.content_type = "application/json"
    data = request.json or {}
    subdomain = re.sub(r"[^a-zA-Z0-9_\-]", "-", data.get("subdomain", "")).strip("-").lower()
    port = str(data.get("port", "")).strip()
    with_auth = bool(data.get("auth", True))

    if not subdomain or len(subdomain) < 2:
        return {"success": False, "error": "Поддомен должен содержать от 2 символов (латиница, цифры, дефис)."}
    if not port.isdigit() or int(port) < 1 or int(port) > 65535:
        return {"success": False, "error": "Укажите корректный порт (1-65535)."}

    c_info = parse_container_info(name)
    target_ip = None
    if c_info.get("ip") and c_info["ip"] != "-":
        target_ip = c_info["ip"].split(",")[0].strip()

    if not target_ip:
        return {"success": False, "error": "Не удалось определить IP контейнера. Запустите контейнер для назначения IP."}

    os.makedirs(CADDY_CONF_DIR, exist_ok=True)
    base_domain = get_base_domain()
    conf_file = os.path.join(CADDY_CONF_DIR, f"lxc-route-{name}-{subdomain}.conf")

    auth_snippet = "    import homelab_auth\n" if with_auth else ""
    conf_content = f"""{subdomain}.{base_domain} {{
{auth_snippet}    reverse_proxy {target_ip}:{port} {{
        header_up Upgrade {{>Upgrade}}
        header_up Connection {{>Connection}}
    }}
}}
"""
    try:
        with open(conf_file, "w", encoding="utf-8") as f:
            f.write(conf_content)
        reload_caddy()
        return {
            "success": True,
            "message": f"Маршрут https://{subdomain}.{base_domain} успешно опубликован!",
            "url": f"https://{subdomain}.{base_domain}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route("/api/containers/<name>/routes/<subdomain>", method="DELETE")
def api_delete_route(name, subdomain):
    response.content_type = "application/json"
    subdomain = re.sub(r"[^a-zA-Z0-9_\-]", "-", subdomain).strip("-").lower()
    conf_file = os.path.join(CADDY_CONF_DIR, f"lxc-route-{name}-{subdomain}.conf")

    if os.path.exists(conf_file):
        try:
            os.remove(conf_file)
            reload_caddy()
            return {"success": True, "message": f"Маршрут для '{subdomain}' удален."}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return {"success": False, "error": "Маршрут не найден."}

# -----------------------------------------------------------------------------
# Phase 3: Inline Rootfs File Explorer & Web Code Editor
# -----------------------------------------------------------------------------

def resolve_container_fs_path(name, req_path):
    rootfs_dir = os.path.join(LXC_DIR, name, "rootfs")
    if not os.path.exists(rootfs_dir):
        return None, None
    safe_rel = os.path.normpath("/" + req_path.lstrip("/")).lstrip("/")
    full_path = os.path.abspath(os.path.join(rootfs_dir, safe_rel))
    if os.path.commonpath([full_path, rootfs_dir]) != rootfs_dir:
        return None, None
    return full_path, "/" + safe_rel

@app.route("/api/containers/<name>/fs/list", method="GET")
def api_fs_list(name):
    response.content_type = "application/json"
    req_path = request.query.get("path", "/")
    full_path, safe_rel = resolve_container_fs_path(name, req_path)

    if not full_path or not os.path.exists(full_path):
        return {"success": False, "error": f"Каталог '{req_path}' не найден."}
    if not os.path.isdir(full_path):
        return {"success": False, "error": "Указанный путь не является каталогом."}

    items = []
    try:
        for entry in os.scandir(full_path):
            try:
                st = entry.stat()
                is_dir = entry.is_dir()
                items.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": st.st_size if not is_dir else 0,
                    "mtime": int(st.st_mtime),
                    "mode": oct(st.st_mode)[-3:]
                })
            except Exception:
                pass
    except PermissionError:
        return {"success": False, "error": "Доступ запрещен (Permission denied)."}

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = os.path.dirname(safe_rel) if safe_rel != "/" else None
    if parent == safe_rel:
        parent = None

    return {
        "success": True,
        "path": safe_rel,
        "parent": parent,
        "items": items
    }

@app.route("/api/containers/<name>/fs/read", method="GET")
def api_fs_read(name):
    response.content_type = "application/json"
    req_path = request.query.get("path", "")
    full_path, safe_rel = resolve_container_fs_path(name, req_path)

    if not full_path or not os.path.isfile(full_path):
        return {"success": False, "error": "Файл не найден."}

    if os.path.getsize(full_path) > 2 * 1024 * 1024:
        return {"success": False, "error": "Файл слишком большой (> 2 МБ) для веб-редактора."}

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {
            "success": True,
            "path": safe_rel,
            "content": content,
            "size": os.path.getsize(full_path)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route("/api/containers/<name>/fs/write", method="POST")
def api_fs_write(name):
    response.content_type = "application/json"
    data = request.json or {}
    req_path = data.get("path", "")
    content = data.get("content", "")
    full_path, safe_rel = resolve_container_fs_path(name, req_path)

    if not full_path:
        return {"success": False, "error": "Некорректный путь к файлу."}

    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "message": f"Файл '{safe_rel}' успешно сохранен!"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route("/api/containers/<name>/fs/download", method="GET")
def api_fs_download(name):
    req_path = request.query.get("path", "")
    full_path, safe_rel = resolve_container_fs_path(name, req_path)

    if not full_path or not os.path.isfile(full_path):
        abort(404, "File not found")

    return static_file(os.path.basename(full_path), root=os.path.dirname(full_path), download=True)

# -----------------------------------------------------------------------------
# Phase 4: Live Telemetry Graphs & Backup Export/Import
# -----------------------------------------------------------------------------

@app.route("/api/containers/<name>/stats", method="GET")
def api_stats(name):
    response.content_type = "application/json"
    now = time.time()
    cg_path = f"/sys/fs/cgroup/lxc.payload.{name}"

    mem_current = 0
    mem_max = "max"
    cpu_usage_usec = 0

    if os.path.exists(cg_path):
        try:
            with open(os.path.join(cg_path, "memory.current"), "r") as f:
                mem_current = int(f.read().strip())
        except Exception:
            pass
        try:
            with open(os.path.join(cg_path, "memory.max"), "r") as f:
                mem_max = f.read().strip()
        except Exception:
            pass
        try:
            with open(os.path.join(cg_path, "cpu.stat"), "r") as f:
                for line in f:
                    if line.startswith("usage_usec"):
                        cpu_usage_usec = int(line.split()[1])
                        break
        except Exception:
            pass

    # Calculate CPU % delta
    cpu_pct = 0.0
    with METRIC_LOCK:
        if name in CPU_METRIC_HISTORY:
            prev_time, prev_usec = CPU_METRIC_HISTORY[name]
            delta_t = now - prev_time
            if delta_t > 0.4:
                delta_u = max(0, cpu_usage_usec - prev_usec)
                cpu_pct = round((delta_u / (delta_t * 1000000)) * 100, 1)
        CPU_METRIC_HISTORY[name] = (now, cpu_usage_usec)

    mem_used_mb = round(mem_current / (1024 * 1024), 1)
    mem_limit_mb = round(int(mem_max) / (1024 * 1024), 1) if mem_max.isdigit() else 0

    return {
        "success": True,
        "name": name,
        "cpu_pct": cpu_pct,
        "mem_used_mb": mem_used_mb,
        "mem_limit_mb": mem_limit_mb,
        "timestamp": int(now * 1000)
    }

@app.route("/api/containers/<name>/export", method="POST")
def api_export(name):
    response.content_type = "application/json"
    src_dir = os.path.join(LXC_DIR, name)
    if not os.path.exists(src_dir):
        return {"success": False, "error": "Container does not exist."}

    os.makedirs(os.path.join(LXC_DIR, "cache"), exist_ok=True)
    archive_name = f"backup-{name}-{int(time.time())}.tar.gz"
    archive_path = os.path.join(LXC_DIR, "cache", archive_name)

    c_info = parse_container_info(name)
    was_running = (c_info.get("state") == "RUNNING")
    if was_running:
        run_cmd(["lxc-stop", "-n", name, "-k"])

    try:
        cmd = ["tar", "-czf", archive_path, "-C", LXC_DIR, name]
        code, out, err = run_cmd(cmd, timeout=300)
        if code == 0:
            return {
                "success": True,
                "message": f"Бэкап '{name}' успешно создан!",
                "download_url": f"/api/containers/backups/{archive_name}"
            }
        return {"success": False, "error": f"Ошибка архивации: {err or out}"}
    finally:
        if was_running:
            run_cmd(["lxc-start", "-n", name, "-d"])

@app.route("/api/containers/backups/<filename>", method="GET")
def api_download_backup(filename):
    cache_dir = os.path.join(LXC_DIR, "cache")
    if not re.match(r"^backup-[a-zA-Z0-9_\-]+\.tar\.gz$", filename):
        abort(400, "Invalid backup filename")
    return static_file(filename, root=cache_dir, download=True)

@app.route("/api/containers/import", method="POST")
def api_import():
    response.content_type = "application/json"
    upload = request.files.get("backup_file")
    new_name = request.forms.get("name", "").strip()
    clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "-", new_name).strip("-").lower()

    if not clean_name or len(clean_name) < 2:
        return {"success": False, "error": "Укажите корректное имя для импортируемого контейнера."}
    if not upload:
        return {"success": False, "error": "Файл архива (.tar.gz) не выбран."}

    target_dir = os.path.join(LXC_DIR, clean_name)
    if os.path.exists(target_dir):
        return {"success": False, "error": f"Контейнер '{clean_name}' уже существует."}

    tmp_archive = f"/tmp/import-{clean_name}-{int(time.time())}.tar.gz"
    try:
        upload.save(tmp_archive, overwrite=True)
        # Extract archive
        tmp_extract = f"/tmp/extract-{clean_name}-{int(time.time())}"
        os.makedirs(tmp_extract, exist_ok=True)
        code, _, err = run_cmd(["tar", "-xzf", tmp_archive, "-C", tmp_extract])
        if code != 0:
            return {"success": False, "error": f"Ошибка распаковки архива: {err}"}

        # Find extracted container dir
        entries = os.listdir(tmp_extract)
        extracted_dir = os.path.join(tmp_extract, entries[0]) if len(entries) == 1 else tmp_extract

        # Move to /var/lib/lxc/<clean_name>
        shutil.move(extracted_dir, target_dir)

        # Fix config
        cfg_path = os.path.join(target_dir, "config")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                content = f.read()
            content = re.sub(r"lxc\.uts\.name\s*=\s*[^\s]+", f"lxc.uts.name = {clean_name}", content)
            content = re.sub(r"lxc\.rootfs\.path\s*=\s*dir:[^\s]+", f"lxc.rootfs.path = dir:{target_dir}/rootfs", content)
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(content)

        ensure_container_dns(clean_name)

        return {"success": True, "message": f"Контейнер '{clean_name}' успешно импортирован!"}
    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return {"success": False, "error": str(e)}
    finally:
        if os.path.exists(tmp_archive):
            os.remove(tmp_archive)
        shutil.rmtree(f"/tmp/extract-{clean_name}", ignore_errors=True)

# -----------------------------------------------------------------------------
# Container Standard Actions
# -----------------------------------------------------------------------------

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
        ensure_container_dns(name)
        code, out, err = run_cmd(["lxc-start", "-n", name, "-d"])
        return {"success": code == 0, "message": "Container started" if code == 0 else err}

    elif action == "stop":
        code, out, err = run_cmd(["lxc-stop", "-n", name, "-k"])
        return {"success": code == 0, "message": "Container stopped" if code == 0 else err}

    elif action == "restart":
        run_cmd(["lxc-stop", "-n", name, "-k"])
        time.sleep(0.5)
        ensure_container_dns(name)
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
        # Clean up associated Caddy routes
        if os.path.exists(CADDY_CONF_DIR):
            prefix = f"lxc-route-{name}-"
            for fname in os.listdir(CADDY_CONF_DIR):
                if fname.startswith(prefix):
                    try:
                        os.remove(os.path.join(CADDY_CONF_DIR, fname))
                    except Exception:
                        pass
            reload_caddy()
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
# Snapshot Management
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

@app.route("/api/containers/<name>/exec", method="POST")
def api_exec(name):
    response.content_type = "application/json"
    data = request.json or {}
    cmd = data.get("command", "").strip()

    if not cmd:
        return {"success": False, "error": "Command cannot be empty."}

    t0 = time.time()
    code, out, err = run_cmd(["lxc-attach", "-n", name, "--", "sh", "-c", cmd], timeout=25)
    dur_ms = round((time.time() - t0) * 1000)

    combined_output = out
    if err:
        combined_output = (combined_output + "\n" + err) if combined_output else err

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
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>📦 LXC Sandbox Manager 2.0</title>
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
                        <span>LXC Manager 2.0</span>
                        <span class="text-[11px] bg-brand-500/20 text-brand-400 border border-brand-500/30 px-2 py-0.5 rounded-full font-mono">Alpine 3.24</span>
                    </h1>
                    <p class="text-[11px] sm:text-xs text-slate-400 truncate">Zero-Docker Sandbox, лимиты cgroup v2, Ingress и файлы</p>
                </div>
            </div>

            <!-- Action Buttons -->
            <div class="flex items-center gap-2 sm:gap-2.5 flex-wrap self-end sm:self-center w-full sm:w-auto">
                <button onclick="openTemplatesModal()" class="flex-1 sm:flex-none flex items-center justify-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs px-3 py-2 rounded-lg transition active:scale-95 shadow-sm font-medium">
                    <span>📥</span> <span>Шаблоны</span>
                </button>
                <button onclick="openImportModal()" class="flex-1 sm:flex-none flex items-center justify-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 text-xs px-3 py-2 rounded-lg transition active:scale-95 shadow-sm font-medium">
                    <span>📤</span> <span>Импорт</span>
                </button>
                <button onclick="openCreateModal()" class="flex-1 sm:flex-none flex items-center justify-center gap-1.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-medium text-xs px-3.5 py-2 rounded-lg shadow-md shadow-blue-600/20 transition-all active:scale-95">
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

        <!-- Host Metrics Bar -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-4">
            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1">
                    <span>CPU Нагрузка</span>
                    <span class="text-blue-400 font-mono text-[10px] sm:text-xs">LoadAvg</span>
                </div>
                <div id="stat-load" class="text-base sm:text-xl font-bold font-mono text-white truncate">0.00, 0.00, 0.00</div>
                <div class="text-[10px] sm:text-xs text-slate-500 mt-1">1, 5, 15 мин</div>
            </div>

            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1">
                    <span>RAM Хоста</span>
                    <span id="stat-ram-pct" class="text-emerald-400 font-mono text-[10px] sm:text-xs">0%</span>
                </div>
                <div id="stat-ram-text" class="text-base sm:text-xl font-bold font-mono text-white truncate">0 / 0 MB</div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 mt-1.5 overflow-hidden">
                    <div id="stat-ram-bar" class="bg-emerald-500 h-1.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
            </div>

            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1">
                    <span>Диск LXC</span>
                    <span id="stat-disk-pct" class="text-cyan-400 font-mono text-[10px] sm:text-xs">0%</span>
                </div>
                <div id="stat-disk-text" class="text-base sm:text-xl font-bold font-mono text-white truncate">0 / 0 GB</div>
                <div class="w-full bg-slate-800 rounded-full h-1.5 mt-1.5 overflow-hidden">
                    <div id="stat-disk-bar" class="bg-cyan-500 h-1.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                </div>
            </div>

            <div class="bg-slate-900 border border-slate-800 rounded-xl p-3 sm:p-4 shadow-sm flex flex-col justify-between">
                <div class="flex items-center justify-between text-[11px] sm:text-xs text-slate-400 mb-1">
                    <span>Контейнеры</span>
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
                    <p class="text-[11px] sm:text-xs text-slate-400 hidden sm:block">Kali, Debian, Ubuntu, Alpine, мгновенное клонирование, Ingress и лимиты ресурсов</p>
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
                            <th class="py-3 px-5">Контейнер</th>
                            <th class="py-3 px-4">Статус</th>
                            <th class="py-3 px-5">Сеть / IP</th>
                            <th class="py-3 px-4">Память / RAM</th>
                            <th class="py-3 px-4">Лимиты cgroup</th>
                            <th class="py-3 px-4">Ingress</th>
                            <th class="py-3 px-4">Автостарт</th>
                            <th class="py-3 px-5 text-right">Действия</th>
                        </tr>
                    </thead>
                    <tbody id="containers-table" class="divide-y divide-slate-800 text-sm">
                        <tr>
                            <td colspan="8" class="py-12 text-center text-slate-500">Загрузка списка контейнеров...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

    </main>

    <!-- Create Container Modal (with Limits & Pre-seed) -->
    <div id="create-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-xl w-full p-4 sm:p-6 shadow-2xl space-y-4 max-h-[92vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 shrink-0">
                <h3 class="font-bold text-white text-base sm:text-lg flex items-center gap-2">
                    <span>⚡</span> Новый Sandbox Контейнер
                </h3>
                <button onclick="closeCreateModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <form id="create-form" onsubmit="handleCreate(event)" class="space-y-4 text-xs sm:text-sm overflow-y-auto pr-1 flex-1">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Имя контейнера</label>
                    <input type="text" id="new-name" required placeholder="например: kali-pentest, dev-node-1" oninput="this.value = this.value.replace(/[^a-zA-Z0-9_\\-]/g, '').toLowerCase()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-xs">
                    <p class="text-[11px] text-slate-500 mt-1">Латиница a-z, цифры и дефис (2-32 символа).</p>
                </div>

                <!-- Distro & Version Select -->
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                        <div class="flex items-center justify-between mb-1">
                            <label class="block text-xs font-medium text-slate-300">Дистрибутив (ОС)</label>
                            <span id="distro-count-badge" class="text-[10px] text-brand-400 font-mono">23 ОС</span>
                        </div>
                        <select id="new-distro" onchange="updateReleaseDropdown()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs truncate">
                        </select>
                    </div>

                    <div>
                        <label class="block text-xs font-medium text-slate-300 mb-1">Версия / Релиз</label>
                        <select id="new-release" onchange="updateCacheHint()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs font-mono">
                        </select>
                    </div>
                </div>

                <div id="distro-cache-hint" class="text-xs pt-0.5"></div>

                <!-- Resource Limits (cgroup v2) -->
                <div class="p-3 bg-slate-950/80 rounded-xl border border-slate-800 space-y-2.5">
                    <label class="block text-xs font-semibold text-white flex items-center justify-between">
                        <span>🎛️ Лимиты ресурсов (cgroup v2):</span>
                        <span class="text-[10px] text-slate-400 font-normal">0 = без ограничений</span>
                    </label>
                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block text-[11px] text-slate-400 mb-1">RAM Limit (MB)</label>
                            <input type="number" id="new-mem-mb" placeholder="например: 512" min="0" max="65536" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white font-mono">
                        </div>
                        <div>
                            <label class="block text-[11px] text-slate-400 mb-1">CPU Quota (%)</label>
                            <input type="number" id="new-cpu-quota" placeholder="например: 50 (50%)" min="0" max="800" class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white font-mono">
                        </div>
                    </div>
                </div>

                <!-- Network Mode Radio -->
                <div class="p-3 bg-slate-950/80 rounded-xl border border-slate-800 space-y-2">
                    <label class="block text-xs font-semibold text-white">Режим сети (Network Mode):</label>
                    <label class="flex items-start gap-2.5 cursor-pointer">
                        <input type="radio" name="net_mode" value="lan" checked onchange="toggleNetModeUI()" class="mt-0.5 text-brand-600 bg-slate-900 border-slate-700 focus:ring-0">
                        <div>
                            <span class="text-xs font-medium text-slate-200">Домашняя сеть LAN (br0 - DHCP от роутера)</span>
                            <p class="text-[11px] text-slate-400">Реальный домашний IP (192.168.1.X), виден в сети как отдельный ПК.</p>
                        </div>
                    </label>
                    <label class="flex items-start gap-2.5 cursor-pointer pt-1">
                        <input type="radio" name="net_mode" value="nat" onchange="toggleNetModeUI()" class="mt-0.5 text-brand-600 bg-slate-900 border-slate-700 focus:ring-0">
                        <div>
                            <span class="text-xs font-medium text-slate-200">Изолированная песочница NAT (lxcbr0 - 10.0.3.x)</span>
                            <p class="text-[11px] text-slate-400">Полигон за файрволом с авто-пробросом в Caddy.</p>
                        </div>
                    </label>
                </div>

                <div id="nat-ip-container" class="hidden">
                    <label class="block text-xs font-medium text-slate-300 mb-1">Статический IPv4 для NAT (lxcbr0)</label>
                    <input type="text" id="new-ip" placeholder="10.0.3.10" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 font-mono text-xs">
                </div>

                <!-- Collapsible Pre-seed / Cloud-Init -->
                <details class="bg-slate-950/60 border border-slate-800 rounded-xl p-3 text-xs">
                    <summary class="font-semibold text-slate-300 cursor-pointer flex items-center justify-between">
                        <span>🔑 Автонастройка (SSH-ключ и Start-скрипт)</span>
                        <span class="text-slate-500 text-[10px]">нажмите для открытия</span>
                    </summary>
                    <div class="space-y-3 pt-3">
                        <div>
                            <label class="block text-[11px] text-slate-400 mb-1">SSH Public Key (для беспарольного входа)</label>
                            <input type="text" id="new-ssh-key" placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5..." class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-[11px] font-mono text-slate-200">
                        </div>
                        <div>
                            <label class="block text-[11px] text-slate-400 mb-1">Post-install скрипт (выполнится при первом старте)</label>
                            <textarea id="new-init-script" rows="2" placeholder="apt update && apt install -y curl htop" class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-[11px] font-mono text-slate-200"></textarea>
                        </div>
                    </div>
                </details>

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
                </div>

                <div class="flex items-center justify-end gap-2.5 pt-3 border-t border-slate-800 shrink-0">
                    <button type="button" id="btn-create-cancel" onclick="closeCreateModal()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Отмена</button>
                    <button type="submit" id="btn-create-submit" class="px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white font-medium rounded-lg shadow-md transition text-xs">Создать Sandbox</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Cgroup v2 Limits Modal -->
    <div id="limits-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-4 sm:p-6 shadow-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-xl">🎛️</span>
                    <h3 class="font-bold text-white text-sm sm:text-base">
                        Лимиты cgroup v2: <span id="limits-target-name" class="text-brand-400 font-mono">name</span>
                    </h3>
                </div>
                <button onclick="closeLimitsModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <form onsubmit="handleSaveLimits(event)" class="space-y-4 text-xs">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Лимит оперативной памяти (RAM Limit)</label>
                    <div class="flex items-center gap-2">
                        <input type="number" id="limits-mem-input" min="0" max="65536" placeholder="0" class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
                        <span class="text-slate-400 font-mono">MB</span>
                    </div>
                    <p class="text-[11px] text-slate-500 mt-1">0 = без лимита. Применяется на лету без перезагрузки!</p>
                </div>

                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Квота процессора (CPU Quota)</label>
                    <div class="flex items-center gap-2">
                        <input type="number" id="limits-cpu-input" min="0" max="800" placeholder="0" class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
                        <span class="text-slate-400 font-mono">%</span>
                    </div>
                    <p class="text-[11px] text-slate-500 mt-1">100 = 1 ядро, 50 = половина ядра, 0 = без ограничений.</p>
                </div>

                <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeLimitsModal()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Отмена</button>
                    <button type="submit" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg text-xs transition">Сохранить лимиты</button>
                </div>
            </form>
        </div>
    </div>

    <!-- 1-Click Caddy Ingress Modal -->
    <div id="ingress-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-xl w-full p-4 sm:p-6 shadow-2xl space-y-4 max-h-[90vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 shrink-0">
                <div class="flex items-center gap-2">
                    <span class="text-xl">🌐</span>
                    <h3 class="font-bold text-white text-sm sm:text-base">
                        Caddy Ingress: <span id="ingress-target-name" class="text-brand-400 font-mono">name</span>
                    </h3>
                </div>
                <button onclick="closeIngressModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <!-- New Route Form -->
            <form onsubmit="handleCreateRoute(event)" class="bg-slate-950 p-3 rounded-xl border border-slate-800 space-y-2.5 shrink-0 text-xs">
                <span class="font-semibold text-slate-200 block">Опубликовать новый порт:</span>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                        <label class="block text-[11px] text-slate-400 mb-1">Желаемый поддомен</label>
                        <div class="flex items-center gap-1">
                            <input type="text" id="route-subdomain-input" required placeholder="my-app" class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white font-mono">
                            <span class="text-slate-500 font-mono text-[11px]" id="route-domain-suffix">.uwu</span>
                        </div>
                    </div>
                    <div>
                        <label class="block text-[11px] text-slate-400 mb-1">Внутренний порт контейнера</label>
                        <input type="number" id="route-port-input" required min="1" max="65535" placeholder="например: 80, 3000, 8080" class="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-white font-mono">
                    </div>
                </div>
                <div class="flex items-center justify-between pt-1">
                    <label class="flex items-center gap-2 cursor-pointer text-slate-300">
                        <input type="checkbox" id="route-auth-check" checked class="rounded bg-slate-900 border-slate-700 text-brand-600 focus:ring-0">
                        <span>Защитить базовой авторизацией (homelab_auth)</span>
                    </label>
                    <button type="submit" id="btn-route-add" class="px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded text-xs transition">+ Опубликовать</button>
                </div>
            </form>

            <!-- Published Routes List -->
            <div class="flex-1 overflow-y-auto space-y-2 min-h-[140px] pr-1" id="ingress-routes-list">
                <div class="text-center py-6 text-slate-500 text-xs">Загрузка маршрутов...</div>
            </div>
        </div>
    </div>

    <!-- Inline Rootfs File Explorer Modal -->
    <div id="files-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-2 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-5xl h-[88vh] shadow-2xl flex flex-col overflow-hidden">
            <!-- Modal Header -->
            <div class="flex items-center justify-between border-b border-slate-800 px-4 py-3 bg-slate-950 shrink-0">
                <div class="flex items-center gap-2 overflow-hidden">
                    <span class="text-lg shrink-0">📁</span>
                    <h3 class="font-bold text-white text-xs sm:text-sm truncate">
                        Файлы rootfs: <span id="files-target-name" class="text-brand-400 font-mono">name</span>
                    </h3>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="closeFilesModal()" class="text-slate-400 hover:text-white text-xl leading-none px-2">&times;</button>
                </div>
            </div>

            <!-- Breadcrumbs Toolbar -->
            <div class="flex items-center justify-between gap-2 px-4 py-2 bg-slate-900/90 border-b border-slate-800 text-xs shrink-0">
                <div class="flex items-center gap-1.5 overflow-x-auto font-mono text-slate-300 py-0.5" id="files-breadcrumbs">
                    <span class="text-slate-500">/</span>
                </div>
                <div class="flex items-center gap-2 shrink-0">
                    <button onclick="navigateFilesUp()" id="btn-files-up" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">⬆ Вверх</button>
                </div>
            </div>

            <!-- Explorer & Editor Area -->
            <div class="flex-1 flex overflow-hidden">
                <!-- Directory Listing Table -->
                <div id="files-table-container" class="flex-1 overflow-y-auto">
                    <table class="w-full text-left border-collapse text-xs">
                        <thead>
                            <tr class="bg-slate-950/40 text-slate-400 border-b border-slate-800 sticky top-0 backdrop-blur">
                                <th class="py-2.5 px-4">Имя</th>
                                <th class="py-2.5 px-4 w-24">Размер</th>
                                <th class="py-2.5 px-4 w-20">Права</th>
                                <th class="py-2.5 px-4 text-right w-28">Действия</th>
                            </tr>
                        </thead>
                        <tbody id="files-tbody" class="divide-y divide-slate-800/60 font-mono text-slate-300">
                            <tr><td colspan="4" class="py-8 text-center text-slate-500">Загрузка каталога...</td></tr>
                        </tbody>
                    </table>
                </div>

                <!-- Text Editor Pane (Hidden by default) -->
                <div id="files-editor-container" class="hidden flex-1 flex flex-col bg-slate-950">
                    <div class="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-slate-900 text-xs">
                        <span id="editor-filename" class="font-mono text-brand-400 font-bold truncate">/etc/hosts</span>
                        <div class="flex items-center gap-2 shrink-0">
                            <button onclick="handleSaveFile()" id="btn-file-save" class="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-medium">💾 Сохранить</button>
                            <button onclick="closeFileEditor()" class="px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs">✕ Закрыть</button>
                        </div>
                    </div>
                    <textarea id="file-editor-textarea" class="flex-1 w-full bg-slate-950 text-slate-100 p-4 font-mono text-xs focus:outline-none resize-none selection:bg-brand-500"></textarea>
                </div>
            </div>
        </div>
    </div>

    <!-- Clone Container Modal -->
    <div id="clone-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-4 sm:p-6 shadow-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-xl">👥</span>
                    <h3 class="font-bold text-white text-sm sm:text-base">
                        Клонировать: <span id="clone-src-name" class="text-brand-400 font-mono">name</span>
                    </h3>
                </div>
                <button onclick="closeCloneModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <form onsubmit="handleCloneSubmit(event)" class="space-y-4 text-xs">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Имя нового клона</label>
                    <input type="text" id="clone-new-name" required placeholder="например: dev-node-clone" oninput="this.value = this.value.replace(/[^a-zA-Z0-9_\\-]/g, '').toLowerCase()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
                    <p class="text-[11px] text-slate-500 mt-1">Через lxc-copy будет создан точный дубликат с новым MAC и IP.</p>
                </div>

                <label class="flex items-center gap-2 cursor-pointer pt-1">
                    <input type="checkbox" id="clone-startnow" checked class="rounded bg-slate-950 border-slate-700 text-brand-600 focus:ring-0">
                    <span class="text-xs text-slate-300">Запустить клон сразу после создания</span>
                </label>

                <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeCloneModal()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Отмена</button>
                    <button type="submit" id="btn-clone-submit" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-lg text-xs transition">Клонировать</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Import Container Modal -->
    <div id="import-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-4 sm:p-6 shadow-2xl space-y-4">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <div class="flex items-center gap-2">
                    <span class="text-xl">📤</span>
                    <h3 class="font-bold text-white text-sm sm:text-base">Импорт контейнера из архива</h3>
                </div>
                <button onclick="closeImportModal()" class="text-slate-400 hover:text-white text-2xl leading-none">&times;</button>
            </div>

            <form onsubmit="handleImportSubmit(event)" class="space-y-4 text-xs">
                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Имя контейнера</label>
                    <input type="text" id="import-name" required placeholder="например: restored-node" oninput="this.value = this.value.replace(/[^a-zA-Z0-9_\\-]/g, '').toLowerCase()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-xs">
                </div>

                <div>
                    <label class="block text-xs font-medium text-slate-300 mb-1">Файл архива (.tar.gz)</label>
                    <input type="file" id="import-file" required accept=".tar.gz,.tgz" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-slate-300 text-xs">
                </div>

                <div id="import-progress" class="hidden p-3 bg-brand-950/40 border border-brand-500/30 rounded-xl space-y-1">
                    <span class="text-blue-300 text-xs font-medium">Распаковка и регистрация контейнера...</span>
                </div>

                <div class="flex items-center justify-end gap-2 pt-3 border-t border-slate-800">
                    <button type="button" onclick="closeImportModal()" class="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Отмена</button>
                    <button type="submit" id="btn-import-submit" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white font-medium rounded-lg text-xs transition">Импортировать</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Templates Catalog Modal -->
    <div id="templates-modal" class="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4 hidden">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-4xl w-full p-4 sm:p-6 shadow-2xl space-y-3.5 max-h-[92vh] flex flex-col">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3 shrink-0">
                <div class="flex items-center gap-2">
                    <span class="text-xl">📥</span>
                    <div>
                        <h3 class="font-bold text-white text-sm sm:text-base">Каталог Шаблонов ОС (LinuxContainers)</h3>
                        <p class="text-[11px] text-slate-400 hidden sm:block">Синхронизация с images.linuxcontainers.org</p>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="refreshUpstreamCatalog()" id="btn-catalog-refresh" class="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs transition active:scale-95" title="Синхронизировать">
                        <span id="cat-refresh-icon">🔄</span>
                        <span class="hidden sm:inline">Обновить из сети</span>
                    </button>
                    <button onclick="closeTemplatesModal()" class="text-slate-400 hover:text-white text-2xl leading-none px-1">&times;</button>
                </div>
            </div>

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

            <div id="template-dl-banner" class="hidden p-3 bg-blue-950/40 border border-blue-500/30 rounded-xl space-y-1 shrink-0">
                <div class="flex items-center justify-between text-xs">
                    <span id="template-dl-text" class="text-blue-300 font-medium flex items-center gap-2">
                        <svg class="animate-spin w-4 h-4 text-blue-400 inline" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
                        Загрузка шаблона...
                    </span>
                    <span class="px-2 py-0.5 rounded text-[10px] bg-blue-500/20 text-blue-300 border border-blue-500/30 font-mono">В процессе</span>
                </div>
            </div>

            <div class="space-y-3 overflow-y-auto flex-1 pr-1" id="templates-list-container">
            </div>

            <div class="flex items-center justify-between pt-3 border-t border-slate-800 text-xs text-slate-500 shrink-0">
                <span class="truncate">Кэш: <code class="text-slate-400 font-mono text-[11px]">/var/cache/lxc/download</code></span>
                <button type="button" onclick="closeTemplatesModal()" class="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs">Закрыть</button>
            </div>
        </div>
    </div>

    <!-- Snapshot Modal -->
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

            <form onsubmit="handleCreateSnapshot(event)" class="flex flex-col sm:flex-row gap-2 shrink-0">
                <input type="text" id="snap-comment-input" placeholder="Комментарий к снимку..." class="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-brand-500 text-xs">
                <button type="submit" id="btn-snap-create" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-medium rounded-lg text-xs whitespace-nowrap transition active:scale-95">+ Сделать снимок</button>
            </form>

            <div class="flex-1 overflow-y-auto space-y-2 min-h-[160px] pr-1" id="snapshots-list">
            </div>
        </div>
    </div>

    <!-- Web Terminal Modal (ttyd) -->
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
        let activeLimitsTarget = '';
        let activeIngressTarget = '';
        let activeFilesTarget = '';
        let activeCloneTarget = '';
        let currentFsPath = '/';
        let currentEditingFile = '';
        let isTermFullscreen = false;
        let catalogData = {};
        let cachedImagesList = [];
        let templatePollTimer = null;
        let hostBaseDomain = 'uwu';

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
                const keyMatch = distKey.toLowerCase().includes(query);
                const nameMatch = (dist.name || '').toLowerCase().includes(query);
                const releaseMatch = (dist.releases || []).some(r => (r.id + ' ' + (r.name || '')).toLowerCase().includes(query));

                if (query && !keyMatch && !nameMatch && !releaseMatch) continue;

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
                    if (!templatePollTimer) templatePollTimer = setInterval(pollTemplateStatus, 2000);
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
                hostBaseDomain = d.base_domain || 'uwu';
                const suffix = document.getElementById('route-domain-suffix');
                if (suffix) suffix.innerText = `.${hostBaseDomain}`;

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
                if (tbody) tbody.innerHTML = `<tr><td colspan="8">${emptyHtml}</td></tr>`;
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

                const limitsBadge = `<span class="text-[11px] font-mono text-slate-300">RAM: ${escapeHtml(c.mem_limit)}</span>`;
                const ingressBadge = c.routes_count > 0 ? 
                    `<button onclick="openIngressModal('${escapeHtml(c.name)}')" class="text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-2 py-0.5 rounded-full font-mono">${c.routes_count} домен(ов)</button>` :
                    `<button onclick="openIngressModal('${escapeHtml(c.name)}')" class="text-[10px] bg-slate-800 hover:bg-slate-700 text-slate-400 px-2 py-0.5 rounded-full border border-slate-700">+ Ingress</button>`;

                // Desktop Table Row
                tableHtml += `
                    <tr class="hover:bg-slate-850/50 transition">
                        <td class="py-3 px-5 font-semibold text-white flex items-center gap-2">
                            <span class="text-base">📦</span>
                            <span class="font-mono">${escapeHtml(c.name)}</span>
                        </td>
                        <td class="py-3 px-4">
                            <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${badgeColor}">
                                <span class="w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-emerald-400 pulse-dot' : 'bg-slate-500'}"></span>
                                ${c.state}
                            </span>
                        </td>
                        <td class="py-3 px-5">
                            <div class="flex items-center gap-2">
                                <span class="font-mono text-slate-300 text-xs">${c.ip !== '-' ? escapeHtml(c.ip) : '<span class="text-slate-600">--</span>'}</span>
                                ${netModeBadge}
                            </div>
                        </td>
                        <td class="py-3 px-4 font-mono text-slate-300 text-xs">${c.memory !== '-' ? escapeHtml(c.memory) : '<span class="text-slate-600">--</span>'}</td>
                        <td class="py-3 px-4 font-mono text-xs">
                            <button onclick="openLimitsModal('${escapeHtml(c.name)}')" class="hover:underline flex items-center gap-1 text-slate-300" title="Нажмите для настройки cgroup лимитов">
                                <span>⚙️</span> ${limitsBadge}
                            </button>
                        </td>
                        <td class="py-3 px-4">${ingressBadge}</td>
                        <td class="py-3 px-4">
                            <button onclick="toggleAutostart('${escapeHtml(c.name)}')" class="text-xs px-2 py-0.5 rounded border transition ${c.autostart ? 'bg-blue-500/10 text-blue-400 border-blue-500/30' : 'bg-slate-800 text-slate-500 border-slate-700'}" title="Переключить">
                                ${c.autostart ? 'Да' : 'Нет'}
                            </button>
                        </td>
                        <td class="py-3 px-5 text-right space-x-1 whitespace-nowrap">
                            ${isRunning ? `
                                <button onclick="openTermModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-emerald-950/50 hover:bg-emerald-900/80 text-emerald-300 rounded text-xs border border-emerald-800/50 transition font-medium" title="Интерактивный шелл">📟</button>
                                <button onclick="openFilesModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700 transition" title="Файлы rootfs">📁</button>
                                <button onclick="openExecModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700 transition" title="Быстрый Exec">💻</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-indigo-950/50 hover:bg-indigo-900/80 text-indigo-300 rounded text-xs border border-indigo-800/50 transition" title="Снимки">📸</button>
                                <button onclick="openCloneModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded text-xs border border-slate-700 transition" title="Клонировать">👥</button>
                                <button onclick="exportContainer('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-amber-300 rounded text-xs border border-slate-700 transition" title="Экспорт бэкапа">📦</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'restart')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-amber-400 rounded text-xs border border-slate-700 transition" title="Перезагрузить">🔄</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'stop')" class="px-2 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 rounded text-xs border border-rose-800/40 transition" title="Остановить">⏹️</button>
                            ` : `
                                <button onclick="openFilesModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs border border-slate-700 transition" title="Файлы rootfs">📁</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-indigo-950/50 hover:bg-indigo-900/80 text-indigo-300 rounded text-xs border border-indigo-800/50 transition" title="Снимки">📸</button>
                                <button onclick="openCloneModal('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded text-xs border border-slate-700 transition" title="Клонировать">👥</button>
                                <button onclick="exportContainer('${escapeHtml(c.name)}')" class="px-2 py-1 bg-slate-800 hover:bg-slate-700 text-amber-300 rounded text-xs border border-slate-700 transition" title="Экспорт бэкапа">📦</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'start')" class="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-medium transition" title="Запустить">▶ Старт</button>
                                <button onclick="confirmDestroy('${escapeHtml(c.name)}')" class="px-2 py-1 bg-rose-950/30 hover:bg-rose-900/60 text-rose-400 rounded text-xs border border-rose-900/40 transition" title="Удалить">🗑️</button>
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
                                <span class="text-slate-500 text-[10px] block">ЛИМИТ RAM</span>
                                <button onclick="openLimitsModal('${escapeHtml(c.name)}')" class="font-mono text-brand-400 text-xs hover:underline">${escapeHtml(c.mem_limit)}</button>
                            </div>
                        </div>

                        <!-- Card Action Buttons Grid -->
                        <div class="grid grid-cols-3 sm:grid-cols-4 gap-1.5 pt-1">
                            ${isRunning ? `
                                <button onclick="openTermModal('${escapeHtml(c.name)}')" class="py-2 bg-emerald-950/60 hover:bg-emerald-900/80 text-emerald-300 rounded-lg text-xs border border-emerald-800/50 font-medium active:scale-95">📟 Шелл</button>
                                <button onclick="openFilesModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs border border-slate-700 active:scale-95">📁 Файлы</button>
                                <button onclick="openIngressModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-blue-300 rounded-lg text-xs border border-slate-700 active:scale-95">🌐 Ingress</button>
                                <button onclick="openLimitsModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-indigo-300 rounded-lg text-xs border border-slate-700 active:scale-95">⚙️ Лимиты</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-purple-300 rounded-lg text-xs border border-slate-700 active:scale-95">📸 Снимки</button>
                                <button onclick="openCloneModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded-lg text-xs border border-slate-700 active:scale-95">👥 Клон</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'restart')" class="py-2 bg-slate-800 hover:bg-slate-700 text-amber-400 rounded-lg text-xs border border-slate-700 active:scale-95">🔄 Рестарт</button>
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'stop')" class="py-2 bg-rose-950/50 hover:bg-rose-900/70 text-rose-300 rounded-lg text-xs border border-rose-800/40 active:scale-95">⏹️ Стоп</button>
                            ` : `
                                <button onclick="handleAction('${escapeHtml(c.name)}', 'start')" class="py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium active:scale-95">▶ Старт</button>
                                <button onclick="openFilesModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs border border-slate-700 active:scale-95">📁 Файлы</button>
                                <button onclick="openSnapshotModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-purple-300 rounded-lg text-xs border border-slate-700 active:scale-95">📸 Снимки</button>
                                <button onclick="openCloneModal('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded-lg text-xs border border-slate-700 active:scale-95">👥 Клон</button>
                                <button onclick="exportContainer('${escapeHtml(c.name)}')" class="py-2 bg-slate-800 hover:bg-slate-700 text-amber-300 rounded-lg text-xs border border-slate-700 active:scale-95">📦 Бэкап</button>
                                <button onclick="confirmDestroy('${escapeHtml(c.name)}')" class="py-2 bg-rose-950/40 hover:bg-rose-900/60 text-rose-400 rounded-lg text-xs border border-rose-900/40 active:scale-95">🗑️ Удалить</button>
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
            if (confirm(`Вы уверены, что хотите удалить контейнер '${name}', его диск и все опубликованные Ingress-маршруты?`)) {
                handleAction(name, 'destroy');
            }
        }

        // --- Creation Modal ---
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
                start_now: document.getElementById('new-startnow').checked,
                memory_mb: parseInt(document.getElementById('new-mem-mb').value || '0', 10),
                cpu_quota: parseInt(document.getElementById('new-cpu-quota').value || '0', 10),
                ssh_key: document.getElementById('new-ssh-key').value.trim(),
                init_script: document.getElementById('new-init-script').value.trim()
            };

            btnSubmit.disabled = true;
            if (btnCancel) btnCancel.disabled = true;
            btnSubmit.classList.add('opacity-50');
            btnSubmit.innerText = 'Развертывание...';
            if (progress) progress.classList.remove('hidden');

            try {
                const res = await fetch('/api/containers/create', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message || `Контейнер '${cleanName}' успешно создан!`, 'success');
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

        // --- Limits Modal ---
        async function openLimitsModal(name) {
            activeLimitsTarget = name;
            document.getElementById('limits-target-name').innerText = name;
            document.getElementById('limits-modal').classList.remove('hidden');

            try {
                const res = await fetch(`/api/containers/${name}/limits`);
                const d = await res.json();
                if (d.success) {
                    document.getElementById('limits-mem-input').value = d.memory_mb || '';
                    document.getElementById('limits-cpu-input').value = d.cpu_quota || '';
                }
            } catch (e) {}
        }

        function closeLimitsModal() {
            document.getElementById('limits-modal').classList.add('hidden');
        }

        async function handleSaveLimits(e) {
            e.preventDefault();
            const mem = parseInt(document.getElementById('limits-mem-input').value || '0', 10);
            const cpu = parseInt(document.getElementById('limits-cpu-input').value || '0', 10);

            try {
                const res = await fetch(`/api/containers/${activeLimitsTarget}/limits`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ memory_mb: mem, cpu_quota: cpu })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    closeLimitsModal();
                    refreshData();
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            }
        }

        // --- Caddy Ingress Modal ---
        async function openIngressModal(name) {
            activeIngressTarget = name;
            document.getElementById('ingress-target-name').innerText = name;
            document.getElementById('ingress-modal').classList.remove('hidden');
            document.getElementById('route-domain-suffix').innerText = `.${hostBaseDomain}`;
            await loadIngressRoutes(name);
        }

        function closeIngressModal() {
            document.getElementById('ingress-modal').classList.add('hidden');
        }

        async function loadIngressRoutes(name) {
            const listEl = document.getElementById('ingress-routes-list');
            listEl.innerHTML = '<div class="text-center py-6 text-slate-500 text-xs">Загрузка маршрутов...</div>';

            try {
                const res = await fetch(`/api/containers/${name}/routes`);
                const d = await res.json();
                const routes = d.routes || [];

                if (routes.length === 0) {
                    listEl.innerHTML = `
                        <div class="text-center py-6 text-slate-500 border border-dashed border-slate-800 rounded-xl">
                            <div class="text-2xl mb-1">🌐</div>
                            <div class="text-xs text-slate-400">Нет опубликованных доменов</div>
                            <div class="text-[11px] text-slate-600 mt-1">Опубликуйте порт выше, чтобы получить HTTPS-домен в локальной сети</div>
                        </div>`;
                    return;
                }

                let html = '';
                for (const r of routes) {
                    html += `
                        <div class="p-3 bg-slate-950 border border-slate-800 rounded-xl flex items-center justify-between text-xs gap-2">
                            <div class="overflow-hidden">
                                <a href="https://${escapeHtml(r.full_domain)}" target="_blank" class="font-mono font-bold text-blue-400 hover:underline flex items-center gap-1.5 truncate">
                                    <span>🔗</span> <span>https://${escapeHtml(r.full_domain)}</span>
                                </a>
                                <div class="text-slate-500 text-[11px] mt-0.5 font-mono">-> контейнер порт ${escapeHtml(r.port)} ${r.auth ? '(🔒 Basic Auth)' : '(Открытый)'}</div>
                            </div>
                            <button onclick="handleDeleteRoute('${escapeHtml(r.subdomain)}')" class="px-2 py-1 bg-rose-950/40 hover:bg-rose-900/60 text-rose-300 rounded border border-rose-800/40 text-[11px] transition active:scale-95 shrink-0">🗑️</button>
                        </div>`;
                }
                listEl.innerHTML = html;
            } catch (err) {
                listEl.innerHTML = `<div class="text-rose-400 text-xs">Ошибка: ${escapeHtml(err.message)}</div>`;
            }
        }

        async function handleCreateRoute(e) {
            e.preventDefault();
            const subdomain = document.getElementById('route-subdomain-input').value.trim();
            const port = document.getElementById('route-port-input').value.trim();
            const auth = document.getElementById('route-auth-check').checked;

            try {
                const res = await fetch(`/api/containers/${activeIngressTarget}/routes`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ subdomain, port, auth })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    document.getElementById('route-subdomain-input').value = '';
                    document.getElementById('route-port-input').value = '';
                    await loadIngressRoutes(activeIngressTarget);
                    refreshData();
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            }
        }

        async function handleDeleteRoute(subdomain) {
            if (!confirm(`Удалить маршрут для поддомена '${subdomain}'?`)) return;
            try {
                const res = await fetch(`/api/containers/${activeIngressTarget}/routes/${subdomain}`, { method: 'DELETE' });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    await loadIngressRoutes(activeIngressTarget);
                    refreshData();
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            }
        }

        // --- File Explorer & Editor Modal ---
        async function openFilesModal(name) {
            activeFilesTarget = name;
            currentFsPath = '/';
            document.getElementById('files-target-name').innerText = name;
            closeFileEditor();
            document.getElementById('files-modal').classList.remove('hidden');
            await loadFsDirectory(currentFsPath);
        }

        function closeFilesModal() {
            document.getElementById('files-modal').classList.add('hidden');
            closeFileEditor();
        }

        async function loadFsDirectory(path) {
            currentFsPath = path;
            renderBreadcrumbs(path);
            const tbody = document.getElementById('files-tbody');
            tbody.innerHTML = '<tr><td colspan="4" class="py-8 text-center text-slate-500">Загрузка каталога...</td></tr>';

            try {
                const res = await fetch(`/api/containers/${activeFilesTarget}/fs/list?path=${encodeURIComponent(path)}`);
                const d = await res.json();
                if (!d.success) {
                    tbody.innerHTML = `<tr><td colspan="4" class="py-8 text-center text-rose-400">${escapeHtml(d.error)}</td></tr>`;
                    return;
                }

                const items = d.items || [];
                if (items.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" class="py-8 text-center text-slate-500">Каталог пуст</td></tr>';
                    return;
                }

                let html = '';
                for (const item of items) {
                    const itemPath = (currentFsPath.endsWith('/') ? currentFsPath : currentFsPath + '/') + item.name;
                    const sizeStr = item.is_dir ? '-' : formatBytes(item.size);
                    const icon = item.is_dir ? '📁' : '📄';

                    html += `
                        <tr class="hover:bg-slate-800/40 transition">
                            <td class="py-2 px-4 flex items-center gap-2 overflow-hidden">
                                <span>${icon}</span>
                                ${item.is_dir ? `
                                    <button onclick="loadFsDirectory('${escapeHtml(itemPath)}')" class="text-blue-400 hover:underline font-bold truncate text-left">${escapeHtml(item.name)}</button>
                                ` : `
                                    <span class="truncate text-slate-200">${escapeHtml(item.name)}</span>
                                `}
                            </td>
                            <td class="py-2 px-4 text-slate-500 text-[11px]">${sizeStr}</td>
                            <td class="py-2 px-4 text-slate-500 text-[11px]">${escapeHtml(item.mode)}</td>
                            <td class="py-2 px-4 text-right space-x-1 whitespace-nowrap">
                                ${!item.is_dir ? `
                                    <button onclick="openFileEditor('${escapeHtml(itemPath)}')" class="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-[11px]" title="Редактировать">✏️</button>
                                    <a href="/api/containers/${encodeURIComponent(activeFilesTarget)}/fs/download?path=${encodeURIComponent(itemPath)}" class="px-2 py-0.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-[11px] inline-block" title="Скачать">📥</a>
                                ` : ''}
                            </td>
                        </tr>`;
                }
                tbody.innerHTML = html;
            } catch (err) {
                tbody.innerHTML = `<tr><td colspan="4" class="py-8 text-center text-rose-400">Ошибка: ${escapeHtml(err.message)}</td></tr>`;
            }
        }

        function renderBreadcrumbs(path) {
            const container = document.getElementById('files-breadcrumbs');
            const parts = path.split('/').filter(Boolean);
            let html = '<button onclick="loadFsDirectory(\'/\')" class="text-slate-400 hover:text-white">/</button>';
            let accum = '';
            for (const p of parts) {
                accum += '/' + p;
                html += ` <span class="text-slate-600">/</span> <button onclick="loadFsDirectory('${escapeHtml(accum)}')" class="text-slate-300 hover:text-white">${escapeHtml(p)}</button>`;
            }
            container.innerHTML = html;
        }

        function navigateFilesUp() {
            if (currentFsPath === '/' || !currentFsPath) return;
            const parent = currentFsPath.substring(0, currentFsPath.lastIndexOf('/')) || '/';
            loadFsDirectory(parent);
        }

        async function openFileEditor(path) {
            currentEditingFile = path;
            document.getElementById('editor-filename').innerText = path;
            const area = document.getElementById('file-editor-textarea');
            area.value = 'Загрузка содержимого файла...';

            document.getElementById('files-table-container').classList.add('hidden');
            document.getElementById('files-editor-container').classList.remove('hidden');

            try {
                const res = await fetch(`/api/containers/${activeFilesTarget}/fs/read?path=${encodeURIComponent(path)}`);
                const d = await res.json();
                if (d.success) {
                    area.value = d.content || '';
                } else {
                    area.value = `Ошибка: ${d.error}`;
                }
            } catch (e) {
                area.value = `Ошибка сети: ${e.message}`;
            }
        }

        function closeFileEditor() {
            document.getElementById('files-editor-container').classList.add('hidden');
            document.getElementById('files-table-container').classList.remove('hidden');
            currentEditingFile = '';
        }

        async function handleSaveFile() {
            if (!currentEditingFile) return;
            const content = document.getElementById('file-editor-textarea').value;
            const btn = document.getElementById('btn-file-save');
            btn.disabled = true;
            btn.innerText = 'Сохранение...';

            try {
                const res = await fetch(`/api/containers/${activeFilesTarget}/fs/write`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ path: currentEditingFile, content })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.innerText = '💾 Сохранить';
            }
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        // --- Clone Modal ---
        function openCloneModal(name) {
            activeCloneTarget = name;
            document.getElementById('clone-src-name').innerText = name;
            document.getElementById('clone-new-name').value = `${name}-clone`;
            document.getElementById('clone-modal').classList.remove('hidden');
        }

        function closeCloneModal() {
            document.getElementById('clone-modal').classList.add('hidden');
        }

        async function handleCloneSubmit(e) {
            e.preventDefault();
            const newName = document.getElementById('clone-new-name').value.trim();
            const startNow = document.getElementById('clone-startnow').checked;
            const btn = document.getElementById('btn-clone-submit');
            btn.disabled = true;
            btn.innerText = 'Клонирование...';

            try {
                const res = await fetch(`/api/containers/${activeCloneTarget}/clone`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ new_name: newName, start_now: startNow })
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    closeCloneModal();
                    refreshData();
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.innerText = 'Клонировать';
            }
        }

        // --- Backup Export & Import ---
        async function exportContainer(name) {
            showToast(`Создание резервной копии '${name}'...`, 'info');
            try {
                const res = await fetch(`/api/containers/${name}/export`, { method: 'POST' });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    window.location.href = d.download_url;
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка экспорта: ${err.message}`, 'error');
            }
        }

        function openImportModal() {
            document.getElementById('import-modal').classList.remove('hidden');
        }

        function closeImportModal() {
            document.getElementById('import-modal').classList.add('hidden');
            const progress = document.getElementById('import-progress');
            if (progress) progress.classList.add('hidden');
        }

        async function handleImportSubmit(e) {
            e.preventDefault();
            const name = document.getElementById('import-name').value.trim();
            const fileInput = document.getElementById('import-file');
            if (!fileInput.files || fileInput.files.length === 0) return;

            const btn = document.getElementById('btn-import-submit');
            const progress = document.getElementById('import-progress');
            btn.disabled = true;
            btn.innerText = 'Импортирование...';
            if (progress) progress.classList.remove('hidden');

            const formData = new FormData();
            formData.append('name', name);
            formData.append('backup_file', fileInput.files[0]);

            try {
                const res = await fetch('/api/containers/import', {
                    method: 'POST',
                    body: formData
                });
                const d = await res.json();
                if (d.success) {
                    showToast(d.message, 'success');
                    closeImportModal();
                    refreshData();
                } else {
                    showToast(d.error, 'error');
                }
            } catch (err) {
                showToast(`Ошибка: ${err.message}`, 'error');
            } finally {
                btn.disabled = false;
                btn.innerText = 'Импортировать';
                if (progress) progress.classList.add('hidden');
            }
        }

        // --- Terminal Modal ---
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

        // --- Snapshot Modal ---
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
                        <div class="text-center py-6 text-slate-500 border border-dashed border-slate-800 rounded-xl">
                            <div class="text-2xl mb-1">📸</div>
                            <div class="text-xs text-slate-400">Нет созданных снимков для '${escapeHtml(name)}'</div>
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
            if (!confirm(`Откатить контейнер '${activeSnapshotTarget}' к снимку '${snapName}'? Несохраненные данные будут перезаписаны.`)) return;

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

        // --- Quick Exec Modal ---
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
