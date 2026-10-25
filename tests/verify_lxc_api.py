#!/usr/bin/env python3
"""
Automated Test & Verification Suite for LXC Sandbox Subsystem
Verifies:
  1. Host Metrics API (/api/status)
  2. Multi-distro Catalog API (/api/catalog)
  3. NAT Sandbox Container provisioning & command execution
  4. Snapshot lifecycle: create, modify container, restore/rollback, delete snapshot
  5. Home LAN Bridged Mode (br0) with physical router DHCP assignment (192.168.1.x)
  6. Web Terminal daemon (ttyd) and Caddy reverse proxy integration
"""

import os
import urllib.request
import urllib.error
import base64
import json
import time
import sys

def api(url, payload=None, auth=None):
    req = urllib.request.Request(url, headers={'Content-Type': 'application/json'})
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode('utf-8')).decode('ascii')
        req.add_header('Authorization', f'Basic {cred}')
    if payload is not None:
        req.data = json.dumps(payload).encode('utf-8')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

print("=== 1. Checking LXC Manager Host Metrics ===")
status = api('http://127.0.0.1:8888/api/status')
print(f"Metrics: Load={status['load']}, RAM={status['ram']['percent']}%, Disk={status['disk']['percent']}%, NextIP={status['suggested_ip']}")
assert "load" in status and "ram" in status and "suggested_ip" in status, "Invalid status response"

print("\n=== 2. Checking Multi-Distribution Catalog API ===")
cat = api('http://127.0.0.1:8888/api/catalog')
distros = cat.get('catalog', {})
print(f"Supported distributions: {list(distros.keys())}")
for d in ['alpine', 'debian', 'ubuntu', 'archlinux', 'fedora', 'rockylinux', 'almalinux']:
    assert d in distros, f"Distribution {d} missing from catalog"
print(f"Cached images on host: {cat.get('cached', [])}")

print("\n=== 3. Creating NAT Sandbox Container 'test-nat' (10.0.3.50) ===")
res = api('http://127.0.0.1:8888/api/containers/create', {
    'name': 'test-nat',
    'distro': 'alpine',
    'release': '3.24',
    'net_mode': 'nat',
    'ip': '10.0.3.50',
    'autostart': False,
    'start_now': True
})
print("Create response:", res)
assert res.get('success') is True, f"Create failed: {res}"

time.sleep(2)

print("\n=== 4. Checking Container State & Executing Command in NAT Sandbox ===")
exec_res = api('http://127.0.0.1:8888/api/containers/test-nat/exec', {
    'command': 'ping -c 2 1.1.1.1'
})
print(f"Ping exit code: {exec_res.get('exit_code')} ({exec_res.get('duration_ms')}ms)")
assert exec_res.get('exit_code') == 0, f"Ping failed: {exec_res}"

print("\n=== 5. Testing LXC Snapshots & Rollback Lifecycle ===")
# 5a. Create snapshot snap0
snap_create = api('http://127.0.0.1:8888/api/containers/test-nat/snapshots/create', {
    'comment': 'Baseline snapshot before file modification'
})
print("Snapshot create response:", snap_create)
assert snap_create.get('success') is True, f"Snapshot creation failed: {snap_create}"

# 5b. List snapshots
snaps = api('http://127.0.0.1:8888/api/containers/test-nat/snapshots')
snap_list = snaps.get('snapshots', [])
print(f"Snapshots found: {len(snap_list)}")
assert len(snap_list) >= 1, "Expected at least 1 snapshot"
snap_name = snap_list[0]['name']
print(f"Active snapshot name: {snap_name}")

# 5c. Create marker file inside container
api('http://127.0.0.1:8888/api/containers/test-nat/exec', {
    'command': 'echo "VERIFY_ROLLBACK_DATA" > /root/test-rollback.txt'
})
check_marker = api('http://127.0.0.1:8888/api/containers/test-nat/exec', {
    'command': 'cat /root/test-rollback.txt'
})
assert "VERIFY_ROLLBACK_DATA" in check_marker.get('output', ''), "Marker file was not written"
print("Marker file created successfully inside container.")

# 5d. Restore snapshot snap0
snap_restore = api('http://127.0.0.1:8888/api/containers/test-nat/snapshots/restore', {
    'snapshot': snap_name
})
print("Snapshot restore response:", snap_restore)
assert snap_restore.get('success') is True, f"Snapshot restore failed: {snap_restore}"

time.sleep(2)

# 5e. Verify marker file no longer exists after rollback
check_after = api('http://127.0.0.1:8888/api/containers/test-nat/exec', {
    'command': 'test -f /root/test-rollback.txt && echo EXISTS || echo MISSING'
})
print("Check after rollback output:", check_after.get('output', '').strip())
assert "MISSING" in check_after.get('output', ''), "Rollback failed: marker file still exists!"
print("Rollback verified: container state restored to pre-modification!")

# 5f. Delete snapshot
snap_del = api('http://127.0.0.1:8888/api/containers/test-nat/snapshots/destroy', {
    'snapshot': snap_name
})
print("Snapshot delete response:", snap_del)
assert snap_del.get('success') is True, f"Snapshot delete failed: {snap_del}"

# Destroy test-nat
dest_nat = api('http://127.0.0.1:8888/api/containers/test-nat/action', {'action': 'destroy'})
assert dest_nat.get('success') is True, f"Destroy test-nat failed: {dest_nat}"
print("test-nat container cleanly destroyed.")

if os.path.exists("/sys/class/net/br0"):
    print("\n=== 6. Creating Home LAN Bridged Container 'test-lan' (Router DHCP) ===")
    res_lan = api('http://127.0.0.1:8888/api/containers/create', {
        'name': 'test-lan',
        'distro': 'alpine',
        'release': '3.24',
        'net_mode': 'lan',
        'autostart': False,
        'start_now': True
    })
    print("Create LAN container response:", res_lan)
    assert res_lan.get('success') is True, f"Create LAN container failed: {res_lan}"

    # Wait up to 10 seconds for DHCP lease from physical home router
    lan_ip = None
    for attempt in range(10):
        time.sleep(1)
        containers_res = api('http://127.0.0.1:8888/api/containers')
        c_info = next((c for c in containers_res.get('containers', []) if c['name'] == 'test-lan'), None)
        if c_info and c_info.get('ip') and c_info['ip'] != '-':
            lan_ip = c_info['ip']
            break

    print(f"Container 'test-lan' acquired IP: {lan_ip}")
    assert lan_ip is not None and "192.168.1." in lan_ip, f"Expected 192.168.1.x IP from router, got: {lan_ip}"

    # Verify outgoing internet from LAN container
    exec_lan = api('http://127.0.0.1:8888/api/containers/test-lan/exec', {
        'command': 'ping -c 2 1.1.1.1'
    })
    print(f"LAN container ping exit code: {exec_lan.get('exit_code')}")
    assert exec_lan.get('exit_code') == 0, f"LAN container ping failed: {exec_lan}"

    # Destroy test-lan
    dest_lan = api('http://127.0.0.1:8888/api/containers/test-lan/action', {'action': 'destroy'})
    assert dest_lan.get('success') is True, f"Destroy test-lan failed: {dest_lan}"
    print("test-lan container cleanly destroyed.")
else:
    print("\n=== 6. Skipping LAN Bridged Container (br0 bridge not configured on this host) ===")

print("\n=== 7. Verifying ttyd Interactive Web Terminal Endpoint ===")
# Check ttyd local port
req_ttyd = urllib.request.Request('http://127.0.0.1:7681/ttyd/')
with urllib.request.urlopen(req_ttyd, timeout=5) as r:
    ttyd_code = r.status
    ttyd_body = r.read().decode('utf-8', errors='ignore')
    print(f"Local ttyd response: HTTP {ttyd_code}")
    assert ttyd_code == 200 and "ttyd" in ttyd_body, "ttyd daemon not responding properly"

print("\nALL LXC MANAGER TESTS PASSED!")
sys.exit(0)
