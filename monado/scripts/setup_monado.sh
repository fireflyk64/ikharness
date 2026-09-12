#!/usr/bin/env bash
# Fetch Monado at the pinned commit, wire in the ikharness driver, build and install it.
#
# Usage: monado/scripts/setup_monado.sh [--no-build]
#
# Environment:
#   MONADO_SRC      where to clone/find Monado           (default: $HOME/dev/monado)
#   MONADO_PREFIX   install prefix                        (default: $HOME/.local/monado-ikharness)
#   MONADO_JOBS     parallel build jobs                   (default: nproc)
#
# Re-running is safe: sources are symlinked (edits in this repo are picked up
# directly) and the patch is only applied once.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IKH_MONADO_DIR="$(cd "$HERE/.." && pwd)"
MONADO_SRC="${MONADO_SRC:-$HOME/dev/monado}"
MONADO_PREFIX="${MONADO_PREFIX:-$HOME/.local/monado-ikharness}"
MONADO_JOBS="${MONADO_JOBS:-$(nproc)}"
COMMIT="$(cat "$IKH_MONADO_DIR/MONADO_COMMIT")"
BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

echo "== Monado source: $MONADO_SRC (commit $COMMIT)"
if [ ! -d "$MONADO_SRC/.git" ]; then
    git clone https://gitlab.freedesktop.org/monado/monado.git "$MONADO_SRC"
fi
if [ "$(git -C "$MONADO_SRC" rev-parse HEAD)" != "$COMMIT" ]; then
    git -C "$MONADO_SRC" fetch --depth 1 origin "$COMMIT"
    git -C "$MONADO_SRC" checkout -q "$COMMIT"
fi

echo "== Linking driver sources"
ln -sfn "$IKH_MONADO_DIR/driver/ikharness" "$MONADO_SRC/src/xrt/drivers/ikharness"
ln -sfn "$IKH_MONADO_DIR/target/target_builder_ikharness.c" "$MONADO_SRC/src/xrt/targets/common/target_builder_ikharness.c"

if ! grep -q XRT_BUILD_DRIVER_IKHARNESS "$MONADO_SRC/CMakeLists.txt"; then
    echo "== Applying registration patch"
    git -C "$MONADO_SRC" apply "$IKH_MONADO_DIR/patches/0001-register-ikharness-driver.patch"
fi

[ "$BUILD" = 1 ] || exit 0

echo "== Configuring"
cmake -S "$MONADO_SRC" -B "$MONADO_SRC/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_INSTALL_PREFIX="$MONADO_PREFIX" \
    -DXRT_MODULE_COMPOSITOR_NULL=ON \
    -DXRT_FEATURE_STEAMVR_PLUGIN=ON \
    -DXRT_MODULE_MONADO_GUI=OFF -DXRT_FEATURE_DEBUG_GUI=OFF \
    -DXRT_BUILD_SAMPLES=OFF \
    -DXRT_BUILD_DRIVER_IKHARNESS=ON \
    -DXRT_BUILD_DRIVER_SIMULATED=ON -DXRT_BUILD_DRIVER_REMOTE=ON \
    -DXRT_BUILD_DRIVER_VIVE=OFF -DXRT_BUILD_DRIVER_WMR=OFF -DXRT_BUILD_DRIVER_RIFT=OFF -DXRT_BUILD_DRIVER_RIFT_S=OFF \
    -DXRT_BUILD_DRIVER_PSVR=OFF -DXRT_BUILD_DRIVER_PSVR2=OFF -DXRT_BUILD_DRIVER_PSMV=OFF -DXRT_BUILD_DRIVER_PSSENSE=OFF \
    -DXRT_BUILD_DRIVER_HYDRA=OFF -DXRT_BUILD_DRIVER_DAYDREAM=OFF -DXRT_BUILD_DRIVER_ARDUINO=OFF -DXRT_BUILD_DRIVER_HDK=OFF \
    -DXRT_BUILD_DRIVER_NS=OFF -DXRT_BUILD_DRIVER_OHMD=OFF -DXRT_BUILD_DRIVER_OPENGLOVES=OFF -DXRT_BUILD_DRIVER_ROKID=OFF \
    -DXRT_BUILD_DRIVER_XREAL_AIR=OFF -DXRT_BUILD_DRIVER_SURVIVE=OFF -DXRT_BUILD_DRIVER_ULV2=OFF -DXRT_BUILD_DRIVER_ULV5=OFF \
    -DXRT_BUILD_DRIVER_VF=OFF -DXRT_BUILD_DRIVER_DEPTHAI=OFF -DXRT_BUILD_DRIVER_REALSENSE=OFF -DXRT_BUILD_DRIVER_EUROC=OFF \
    -DXRT_BUILD_DRIVER_HANDTRACKING=OFF -DXRT_BUILD_DRIVER_TWRAP=OFF -DXRT_BUILD_DRIVER_V4L2=OFF -DXRT_BUILD_DRIVER_UVC=OFF \
    -DXRT_BUILD_DRIVER_STEAMVR_LIGHTHOUSE=OFF -DXRT_BUILD_DRIVER_SOLARXR=OFF -DXRT_BUILD_DRIVER_QWERTY=OFF \
    -DXRT_BUILD_DRIVER_SIMULAVR=OFF -DXRT_BUILD_DRIVER_CONTACTGLOVE=OFF -DXRT_BUILD_DRIVER_BLUBUR_S1=OFF \
    -DXRT_BUILD_DRIVER_RIFT_SENSOR=OFF -DXRT_BUILD_DRIVER_ANDROID=OFF -DXRT_BUILD_DRIVER_ILLIXR=OFF

echo "== Building with $MONADO_JOBS jobs"
ninja -C "$MONADO_SRC/build" -j "$MONADO_JOBS"

echo "== Installing to $MONADO_PREFIX"
ninja -C "$MONADO_SRC/build" install

echo
echo "Done. OpenXR runtime manifest: $MONADO_PREFIX/share/openxr/1/openxr_monado.json"
echo "Start the service with: monado/scripts/run_service.sh"
