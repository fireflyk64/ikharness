#!/usr/bin/env bash
# Run monado-service headless with the ikharness driver.
#
# Usage: monado/scripts/run_service.sh [extra monado-service args]
#
# Environment (all optional):
#   MONADO_PREFIX    install prefix from setup_monado.sh   (default: $HOME/.local/monado-ikharness)
#   IKH_CONFIG       driver config JSON                    (default: monado/config/ikharness.json)
#   IKH_PORT         override the listen port
#   IKH_BIND         override the bind address (0.0.0.0 to accept remote orchestrators)
#   IKH_LOG          driver log level: trace|debug|info|warn|error
#   IKH_COMPOSITOR   "null" (default, no window/GPU output) or "main" (real compositor, needs a display)
#
# Clients need XR_RUNTIME_JSON pointing at the same prefix, which this script prints.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONADO_PREFIX="${MONADO_PREFIX:-$HOME/.local/monado-ikharness}"
SERVICE="$MONADO_PREFIX/bin/monado-service"
[ -x "$SERVICE" ] || { echo "monado-service not found at $SERVICE, run setup_monado.sh first" >&2; exit 1; }

export IKH_CONFIG="${IKH_CONFIG:-$HERE/../config/ikharness.json}"
export IKH_ENABLE="${IKH_ENABLE:-1}"
export IKH_LOG="${IKH_LOG:-info}"
# Do not poll stdin (it is not pollable under CI runners / process managers).
export XRT_NO_STDIN="${XRT_NO_STDIN:-1}"
export XRT_COMPOSITOR_NULL="$([ "${IKH_COMPOSITOR:-null}" = "null" ] && echo 1 || echo 0)"
export XR_RUNTIME_JSON="$MONADO_PREFIX/share/openxr/1/openxr_monado.json"

# Monado puts its IPC socket in XDG_RUNTIME_DIR; give it a private one if the
# session has none (containers, CI). Clients must see the same value.
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    export XDG_RUNTIME_DIR="/tmp/ikharness-runtime-$(id -u)"
    mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
    echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR (defaulted, export it for clients too)"
fi

echo "XR_RUNTIME_JSON=$XR_RUNTIME_JSON"
echo "IKH_CONFIG=$IKH_CONFIG"
exec "$SERVICE" "$@"
