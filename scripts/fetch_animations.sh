#!/usr/bin/env bash
# Clone the external animation sources used by suites/default.json at their pinned commits.
# Usage: scripts/fetch_animations.sh [--with-demo]
#   IKH_DATA_DIR   destination (default ~/dev/animations)
set -euo pipefail
DIR="${IKH_DATA_DIR:-$HOME/dev/animations}"
mkdir -p "$DIR"
fetch() {  # url dir commit
    local url="$1" dest="$DIR/$2" commit="$3"
    if [ ! -d "$dest/.git" ]; then
        echo "== cloning $url"
        git clone -q --depth 1 "$url" "$dest"
    fi
    if [ -n "$commit" ] && [ "$(git -C "$dest" rev-parse --short HEAD)" != "$commit" ]; then
        git -C "$dest" fetch -q --depth 1 origin "$commit" && git -C "$dest" checkout -q "$commit"
    fi
    echo "   $2 @ $(git -C "$dest" rev-parse --short HEAD)"
}
fetch https://github.com/V-Sekai/ANIM_test_assets.git        ANIM_test_assets    d28a887
fetch https://github.com/V-Sekai-fire/ANIM_perfume.git       ANIM_perfume        79108f5
fetch https://github.com/V-Sekai/ANIM_mmd_vrm_sample.git     ANIM_mmd_vrm_sample e0e870d
if [ "${1:-}" = "--with-demo" ]; then
    fetch https://github.com/V-Sekai-fire/ANIM_female_doll_retargeting.git ANIM_female_doll_retargeting ""
fi
echo "data in $DIR"
