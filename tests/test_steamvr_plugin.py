"""Monado's SteamVR plugin (driver_monado.so) inside a mock vrserver.

SteamVR itself is not needed: monado/tests/steamvr_mock_host.cpp plays the driver host,
loads the plugin, activates every device it adds and records what it is told. This covers
monado/patches/0004 (generic trackers forwarded with their roles, per-eye render target
size) and the driver's projection as SteamVR would see it.
"""

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ikharness.proc import Guard, ensure_headroom
from ikharness.protocol import Pose, wait_for_driver
from ikharness.service import monado_prefix

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = monado_prefix() / "share/steamvr-monado/bin/linux64/driver_monado.so"
OPENVR_INCLUDES = Path(os.environ.get("MONADO_SRC", Path.home() / "dev/monado")) / "src/external/openvr_includes"
PORT = int(os.environ.get("IKH_STEAMVR_TEST_PORT", "4363"))

pytestmark = pytest.mark.skipif(
    not PLUGIN.exists() or not shutil.which("g++") or not (OPENVR_INCLUDES / "openvr_driver.h").exists(),
    reason="needs the built SteamVR plugin, g++ and Monado's OpenVR headers")


@pytest.fixture(scope="module")
def mock_host(tmp_path_factory):
    exe = tmp_path_factory.mktemp("mock") / "steamvr_mock_host"
    subprocess.run(["g++", "-std=c++17", "-O1", "-I", str(OPENVR_INCLUDES), str(ROOT / "monado/tests/steamvr_mock_host.cpp"),
                    "-o", str(exe), "-ldl", "-lpthread"], check=True, capture_output=True, timeout=300)
    return exe


@pytest.fixture(scope="module")
def report(mock_host):
    """Run the plugin, feed poses through the ikharness wire protocol, return the host's report."""
    ensure_headroom()
    env = dict(os.environ, IKH_ENABLE="1", IKH_PORT=str(PORT), IKH_CONFIG=str(ROOT / "monado/config/ikharness.json"),
               IKH_LOG="warn", XRT_LOG="warn")
    proc = subprocess.Popen([str(mock_host), str(PLUGIN), "15"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    guard = Guard(proc, max_rss_mb=1500)
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready", ready
        with wait_for_driver(port=PORT, timeout=15) as client:
            client.send_frame({
                "hmd": Pose((0.0, 1.6, 0.1)),
                "left_hand": Pose((-0.3, 1.0, -0.2)),
                "waist": Pose((0.02, 0.95, -0.03), (0.0, math.sin(0.2), 0.0, math.cos(0.2))),
                "left_foot": Pose((-0.1, 0.1, 0.0)),
                "right_knee": Pose.disconnected(),
            }, frame_id=1)
        proc.stdin.write("go\n")
        proc.stdin.flush()
        doc = json.loads(proc.stdout.readline())
        proc.wait(timeout=20)
        return doc
    finally:
        guard.stop()


def by_serial(report):
    return {d["serial"]: d for d in report["devices"]}


def test_plugin_adds_hmd_controllers_and_generic_trackers(report):
    classes = [d["class"] for d in report["devices"]]
    assert classes.count("hmd") == 1 and classes.count("controller") == 2 and classes.count("generic_tracker") == 8
    assert all(d["activated"] and d["activate_error"] == 0 for d in report["devices"])
    devices = by_serial(report)
    assert devices["IKH-TRK-waist"]["controller_type"] == "vive_tracker_waist"
    assert devices["IKH-TRK-left_foot"]["controller_type"] == "vive_tracker_left_foot"
    assert devices["IKH-TRK-right_elbow"]["controller_type"] == "vive_tracker_right_elbow"
    # The role is also stored where SteamVR keeps tracker roles.
    assert report["settings"]["trackers|/devices/monado/IKH-TRK-waist"] == "TrackerRole_Waist"
    assert report["settings"]["trackers|/devices/monado/IKH-TRK-left_knee"] == "TrackerRole_LeftKnee"


def test_poses_sent_to_the_driver_reach_the_host(report):
    devices = by_serial(report)
    waist = devices["IKH-TRK-waist"]["pose"]
    assert waist["valid"] and waist["connected"]
    assert waist["position"] == pytest.approx([0.02, 0.95, -0.03], abs=1e-5)
    assert waist["rotation_xyzw"] == pytest.approx([0.0, math.sin(0.2), 0.0, math.cos(0.2)], abs=1e-5)
    assert devices["IKH-TRK-left_foot"]["pose"]["position"] == pytest.approx([-0.1, 0.1, 0.0], abs=1e-5)
    assert devices["IKH-CTRL-L"]["pose"]["position"] == pytest.approx([-0.3, 1.0, -0.2], abs=1e-5)
    hmd = next(d for d in report["devices"] if d["class"] == "hmd")
    assert hmd["pose"]["position"] == pytest.approx([0.0, 1.6, 0.1], abs=1e-5)
    # A tracker the orchestrator dropped shows up as disconnected, not at a stale pose.
    knee = devices["IKH-TRK-right_knee"]["pose"]
    assert not knee["connected"] and not knee["valid"]
    assert all(d["pose_updates"] >= 10 for d in report["devices"])


def test_eye_render_target_matches_the_field_of_view(report):
    """A square frustum needs a square per-eye target (upstream reported the whole 2:1 screen)."""
    hmd = report["hmd"]
    width, height = hmd["recommended_render_target"]
    for eye in hmd["eyes"]:
        left, right, top, bottom = eye["projection_raw"]
        assert left == pytest.approx(-right, rel=1e-4) and top == pytest.approx(-bottom, rel=1e-4)
        fov_aspect = (right - left) / (bottom - top)
        assert width / height == pytest.approx(fov_aspect, rel=0.01)
        assert eye["viewport"][2] / eye["viewport"][3] == pytest.approx(fov_aspect, rel=0.01)
        assert math.degrees(2 * math.atan(right)) == pytest.approx(100.0, abs=0.1)
    assert hmd["eyes"][0]["viewport"][0] == 0 and hmd["eyes"][1]["viewport"][0] == hmd["eyes"][0]["viewport"][2]
