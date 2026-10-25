#!/bin/sh
set -e

# ==============================================================================
# Alpine Homelab Appliance - One-Line Bootstrap Installer
# Usage (Public repo):
#   sh -c "$(curl -fsSL https://raw.githubusercontent.com/unknownpeace/alpine-homelab/master/bootstrap.sh)"
# Usage (Private repo with Personal Access Token):
#   GH_TOKEN="ghp_xxx" sh -c "$(curl -fsSL -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github.v3.raw" https://api.github.com/repos/unknownpeace/alpine-homelab/contents/bootstrap.sh)"
# ==============================================================================

REPO_URL="${REPO_URL:-https://github.com/unknownpeace/alpine-homelab.git}"
TARGET_DIR="${TARGET_DIR:-/opt/homelab}"

# Support private GitHub clone when GH_TOKEN is provided
CLONE_URL="$REPO_URL"
if [ -n "${GH_TOKEN:-}" ]; then
    CLONE_URL="https://${GH_TOKEN}@github.com/unknownpeace/alpine-homelab.git"
fi

echo "================================================================="
echo "    Alpine Homelab Appliance - Bootstrap Installer              "
echo "================================================================="

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This installer must be executed as root." >&2
    exit 1
fi

# 1. Ensure git and curl are present on Alpine
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    echo "[*] Installing required bootstrap dependencies (git, curl)..."
    if ! grep -q "^http.*/community" /etc/apk/repositories 2>/dev/null; then
        sed -i '/^#.*\/community/s/^#//' /etc/apk/repositories || true
    fi
    apk update
    apk add --no-cache git curl
fi

# 2. Clone or Update Repository
if [ -d "$TARGET_DIR/.git" ]; then
    echo "[*] Updating existing repository in $TARGET_DIR..."
    git -C "$TARGET_DIR" pull --ff-only || echo "[!] Notice: Local changes present, continuing with local version."
elif [ -d "$TARGET_DIR" ]; then
    echo "[!] Target directory $TARGET_DIR exists but is not a git repository. Backing up..."
    mv "$TARGET_DIR" "${TARGET_DIR}.bak.$(date +%s)"
    git clone "$CLONE_URL" "$TARGET_DIR"
else
    echo "[*] Cloning repository to $TARGET_DIR..."
    mkdir -p "$(dirname "$TARGET_DIR")"
    git clone "$CLONE_URL" "$TARGET_DIR"
fi

# 3. Create global symlink
ln -sf "$TARGET_DIR/homelab.sh" /usr/local/bin/homelab
chmod +x "$TARGET_DIR/homelab.sh"

echo "[*] Launching modular installation orchestrator..."
exec /usr/local/bin/homelab install "$@"
