#!/usr/bin/env bash
# One-shot setup of everything ikharness needs, idempotent. Run from anywhere.
#
#   scripts/setup.sh                 venv + Python deps, Godot 4.7 binary, harness class cache, data repos
#   scripts/setup.sh --with-monado   ... plus the Monado build (~20 min, Linux only, needs sudo for apt)
#   scripts/setup.sh --apt           install the Debian/Ubuntu packages first (needs sudo)
#
# Environment: GODOT (existing binary to use), IKH_DATA_DIR (where animation repos go),
#              MONADO_PREFIX (install prefix for Monado).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WITH_MONADO=0; APT=0
for a in "$@"; do case "$a" in --with-monado) WITH_MONADO=1 ;; --apt) APT=1 ;; esac; done

if [ "$APT" = 1 ]; then
    echo "== apt packages"
    sudo apt-get install -y python3-venv python3-pip xvfb mesa-vulkan-drivers libgl1-mesa-dri git git-lfs unzip curl
    if [ "$WITH_MONADO" = 1 ]; then
        sudo apt-get install -y cmake ninja-build build-essential pkg-config libeigen3-dev libvulkan-dev vulkan-tools \
            glslang-tools libopenxr-dev libopenxr-loader1 libcjson-dev libbsd-dev libsystemd-dev libx11-xcb-dev libxrandr-dev \
            libxcb-randr0-dev libgl1-mesa-dev libegl1-mesa-dev libwayland-dev wayland-protocols libudev-dev libusb-1.0-0-dev libhidapi-dev
    fi
fi

echo "== Python venv"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-lock.txt
.venv/bin/pip install -q -e .

echo "== Godot $(cat godot/GODOT_VERSION)"
GODOT_VERSION="$(cat godot/GODOT_VERSION)"
if [ -z "${GODOT:-}" ]; then
    GODOT_BIN="$HOME/dev/tools/godot/Godot_v${GODOT_VERSION}_linux.x86_64"
    if [ ! -x "$GODOT_BIN" ]; then
        mkdir -p "$(dirname "$GODOT_BIN")"
        url="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/Godot_v${GODOT_VERSION}_linux.x86_64.zip"
        echo "   downloading $url"
        curl -sL -o /tmp/godot.zip "$url" && unzip -q -o /tmp/godot.zip -d "$(dirname "$GODOT_BIN")" && rm /tmp/godot.zip
    fi
    mkdir -p "$HOME/.local/bin" && ln -sfn "$GODOT_BIN" "$HOME/.local/bin/godot"
    export GODOT="$GODOT_BIN"
fi
"$GODOT" --headless --version | tail -1

echo "== Godot harness class cache"
"$GODOT" --headless --path godot/harness --import >/dev/null 2>&1 || true
[ -f godot/harness/.godot/global_script_class_cache.cfg ] && echo "   ok" || echo "   WARNING: class cache missing"

echo "== animation data"
scripts/fetch_animations.sh

if [ "$WITH_MONADO" = 1 ]; then
    echo "== Monado"
    monado/scripts/setup_monado.sh
fi

echo
echo "Done. Try:  scripts/ikh status"
