"""Start and stop a guarded ``monado-service`` with the ikharness driver.

    with MonadoService(eye=(256, 256)) as svc:
        client = svc.connect()          # IkhClient
        ... run an OpenXR application with env=svc.client_env() ...

The service runs in its own session under the memory guard, with the null compositor (no
window) unless ``compositor="main"``. A temporary driver config sets the eye size, which
should be small for software-rendered clients.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from .proc import Guard, ensure_headroom
from .protocol import IkhClient, wait_for_driver

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACKERS = ["waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow", "right_elbow"]


def monado_prefix() -> Path:
    return Path(os.environ.get("MONADO_PREFIX", Path.home() / ".local" / "monado-ikharness"))


def runtime_json() -> Path:
    return Path(os.environ.get("XR_RUNTIME_JSON", monado_prefix() / "share/openxr/1/openxr_monado.json"))


def runtime_dir() -> str:
    d = os.environ.get("XDG_RUNTIME_DIR")
    if not d:
        d = f"/tmp/ikharness-runtime-{os.getuid()}"
        Path(d).mkdir(mode=0o700, exist_ok=True)
        os.environ["XDG_RUNTIME_DIR"] = d
    return d


class MonadoService:
    def __init__(self, port: int = 4343, eye: Tuple[int, int] = (1024, 1024), fov_h_deg: float = 100.0,
                 trackers: Sequence[str] = DEFAULT_TRACKERS, compositor: str = "null", log_path: Optional[Path] = None,
                 max_rss_mb: int = 1500, refresh_hz: float = 90.0):
        self.port = port
        self.eye = eye
        self.fov_h_deg = fov_h_deg
        self.trackers = list(trackers)
        self.compositor = compositor
        self.log_path = Path(log_path) if log_path else ROOT / "out" / "monado-service.log"
        self.max_rss_mb = max_rss_mb
        self.refresh_hz = refresh_hz
        self.proc: Optional[subprocess.Popen] = None
        self.guard: Optional[Guard] = None
        self._tmp: Optional[tempfile.TemporaryDirectory] = None
        self._log = None

    def client_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Environment for OpenXR applications that should use this runtime."""
        env = dict(os.environ)
        env["XR_RUNTIME_JSON"] = str(runtime_json())
        env["XDG_RUNTIME_DIR"] = runtime_dir()
        if extra:
            env.update(extra)
        return env

    def start(self) -> "MonadoService":
        service = monado_prefix() / "bin" / "monado-service"
        if not service.exists():
            raise FileNotFoundError(f"{service} not found; run monado/scripts/setup_monado.sh")
        ensure_headroom()
        self._tmp = tempfile.TemporaryDirectory(prefix="ikh-service-")
        cfg = {
            "version": 1, "port": self.port, "bind": "127.0.0.1",
            "hmd": {"eye_width": self.eye[0], "eye_height": self.eye[1], "view_count": 2, "fov_h_deg": self.fov_h_deg,
                    "ipd_m": 0.063, "refresh_hz": self.refresh_hz},
            "controllers": "index", "trackers": self.trackers,
        }
        cfg_path = Path(self._tmp.name) / "ikharness.json"
        cfg_path.write_text(json.dumps(cfg))
        env = self.client_env({
            "IKH_CONFIG": str(cfg_path), "IKH_ENABLE": "1", "IKH_PORT": str(self.port),
            "IKH_LOG": os.environ.get("IKH_LOG", "info"), "XRT_LOG": os.environ.get("XRT_LOG", "warn"),
            "XRT_NO_STDIN": "1", "XRT_COMPOSITOR_NULL": "1" if self.compositor == "null" else "0",
            "LP_NUM_THREADS": os.environ.get("LP_NUM_THREADS", "1"),
        })
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen([str(service)], env=env, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
        self.guard = Guard(self.proc, max_rss_mb=self.max_rss_mb)
        try:
            wait_for_driver(port=self.port, timeout=30).close()
        except Exception:
            self.stop()
            raise RuntimeError(f"monado-service did not come up, see {self.log_path}:\n{self.log_path.read_text()[-2000:]}")
        return self

    def connect(self) -> IkhClient:
        return IkhClient(port=self.port)

    def stop(self) -> None:
        if self.guard is not None:
            self.guard.stop()
            self.guard = None
        if self.proc is not None:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self) -> "MonadoService":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
