#!/bin/sh
set -e
# Backward-compatible entrypoint for Alpine Homelab Bare-Metal Appliance
# Delegates execution to the modular orchestrator: homelab.sh
SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
exec "$SCRIPT_DIR/homelab.sh" install "$@"
