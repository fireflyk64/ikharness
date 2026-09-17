import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

PREFIX = Path(os.environ.get("MONADO_PREFIX", Path.home() / ".local/monado-ikharness"))
IKH_PORT = int(os.environ.get("IKH_PORT", "4343"))


def _runtime_json() -> Path:
    if os.environ.get("XR_RUNTIME_JSON"):
        return Path(os.environ["XR_RUNTIME_JSON"])
    installed = PREFIX / "share/openxr/1/openxr_monado.json"
    if installed.exists():
        return installed
    dev = Path(os.environ.get("MONADO_SRC", Path.home() / "dev/monado")) / "build/openxr_monado-dev.json"
    return dev


@pytest.fixture(scope="session")
def monado_service():
    """Start monado-service with the ikharness driver, or use an external one.

    Set IKH_EXTERNAL_SERVICE=1 to test against an already running service.
    """
    runtime_json = _runtime_json()
    if not runtime_json.exists():
        pytest.skip(f"Monado runtime manifest not found ({runtime_json}); build it with monado/scripts/setup_monado.sh")
    os.environ["XR_RUNTIME_JSON"] = str(runtime_json)
    if not os.environ.get("XDG_RUNTIME_DIR"):
        rt = Path(f"/tmp/ikharness-runtime-{os.getuid()}")
        rt.mkdir(mode=0o700, exist_ok=True)
        os.environ["XDG_RUNTIME_DIR"] = str(rt)

    if os.environ.get("IKH_EXTERNAL_SERVICE") == "1":
        yield None
        return

    service = PREFIX / "bin/monado-service"
    if not service.exists():
        service = Path(os.environ.get("MONADO_SRC", Path.home() / "dev/monado")) / "build/src/xrt/targets/service/monado-service"
    if not service.exists():
        pytest.skip("monado-service binary not found")

    env = dict(os.environ)
    env.setdefault("XRT_COMPOSITOR_NULL", "1")
    env.setdefault("XRT_NO_STDIN", "1")
    env.setdefault("IKH_CONFIG", str(ROOT / "monado/config/ikharness.json"))
    env["IKH_PORT"] = str(IKH_PORT)
    env.setdefault("IKH_LOG", "info")
    env.setdefault("XRT_LOG", "warn")
    log_path = ROOT / "out/monado-service.log"
    log_path.parent.mkdir(exist_ok=True)
    log = open(log_path, "w")
    from ikharness.proc import Guard, ensure_headroom
    ensure_headroom()
    proc = subprocess.Popen([str(service)], env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    guard = Guard(proc, max_rss_mb=1500)
    try:
        # Give the driver a moment to bind its socket, the tests then retry connecting.
        time.sleep(0.5)
        if proc.poll() is not None:
            log.close()
            pytest.fail(f"monado-service exited early, see {log_path}:\n{log_path.read_text()[-4000:]}")
        yield proc
    finally:
        guard.stop()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
