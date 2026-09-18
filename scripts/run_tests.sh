#!/usr/bin/env bash
# Run the test suite in parts, one heavy component at a time, reporting container headroom
# between parts (see docs/resources.md). Usage: scripts/run_tests.sh [python|godot|gpu|retarget|monado ...]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY=".venv/bin/python"
export GODOT="${GODOT:-$HOME/.local/bin/godot}"
parts=("$@")
[ ${#parts[@]} -eq 0 ] && parts=(python godot gpu retarget monado)
rc=0
headroom() { "$PY" -c "from ikharness.proc import container_free_mb as f; v=f(); print('   container free: ' + ('unlimited' if v is None else f'{v:.0f} MB'))"; }
for part in "${parts[@]}"; do
    case "$part" in
        python)   files="tests/test_dataset_pipeline.py tests/test_protocol.py tests/test_negative.py tests/test_proc.py tests/test_shadermotion_codec.py tests/test_shadermotion_pose.py tests/test_cli.py" ;;
        godot)    files="tests/test_godot_harness.py" ;;
        gpu)      files="tests/test_godot_gpu.py" ;;
        retarget) files="tests/test_retarget.py" ;;
        monado)   files="tests/test_monado_driver.py" ;;
        *) echo "unknown part $part"; exit 2 ;;
    esac
    echo "== $part"; headroom
    "$PY" -m pytest -q $files || rc=1
done
headroom
exit $rc
