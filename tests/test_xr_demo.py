"""The whole OpenXR chain: monado-service, the Godot XR demo under Xvfb, the calibration
gesture, tracker replay, and poses read off the screen (see docs/godot-openxr.md).

Needs the Monado build, Godot, Xvfb and software OpenGL; skipped when one is missing.
"""

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from ikharness.run_godot import evaluate
from ikharness.service import monado_prefix
from ikharness.shadermotion.readout import load_image
from ikharness.xr import HARNESS, run, strip_region

DATA = Path(__file__).parent / "data"
PORT = int(os.environ.get("IKH_XR_TEST_PORT", "4353"))

pytestmark = pytest.mark.skipif(
    not (monado_prefix() / "bin" / "monado-service").exists() or not shutil.which(os.environ.get("GODOT", "godot"))
    or not shutil.which("Xvfb"),
    reason="needs monado-service (monado/scripts/setup_monado.sh), godot and Xvfb")


def test_action_map_has_the_profiles_the_driver_offers():
    text = (HARNESS / "openxr_action_map.tres").read_text()
    for profile in ("valve/index_controller", "oculus/touch_controller", "khr/simple_controller"):
        assert f"/interaction_profiles/{profile}" in text
    assert "/user/hand/left/input/trigger/value" in text
    assert "/interaction_profiles/htc/vive_tracker_htcx" in text
    assert "/user/vive_tracker_htcx/role/left_foot/input/grip/pose" in text


@pytest.fixture(scope="module")
def xr_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("xr")
    report, info = run(DATA / "mini_walk.json", "6pt", "builtin", out, port=PORT, log=lambda *_: None,
                       video="x264-crf23" if shutil.which("ffmpeg") else None)
    return out, report, info


def test_calibration_gesture_reaches_the_demo(xr_run):
    _, _, info = xr_run
    cal = info["calibration"]
    # Replay turns the avatar (faces +Z) to face -Z of the stage: the demo must find that.
    assert abs(abs(cal["root"]["yaw_deg"]) - 180.0) < 0.01
    assert np.allclose(cal["root"]["position"], [0, 0, 0], atol=1e-4)
    assert abs(cal["height_ratio"] - 1.0) < 1e-3
    assert sorted(cal["roles"]) == sorted(["head", "left_hand", "right_hand", "waist", "left_foot", "right_foot"])
    # Body trackers arrive through OpenXR (XR_HTCX_vive_tracker_interaction roles), not the side channel.
    assert info["body_source"] == {"waist": "openxr", "left_foot": "openxr", "right_foot": "openxr"}


def test_screen_frames_hold_the_slots_and_the_spectator_view(xr_run):
    out, _, info = xr_run
    files = sorted(Path(info["frame_dir"]).glob("frame_*.png"))
    assert len(files) == 3 and info["unstable_frames"] == 0
    image = load_image(files[0])
    assert image.shape == (360, 640, 3)
    x, y, w, h = strip_region((640, 360))
    strip = image[y:y + h, x:x + w]
    assert len(np.unique(strip.reshape(-1, 3), axis=0)) > 30       # slot colors
    assert image[5, 600].tolist() != [0, 0, 0]                     # the sky of the spectator view
    doc = json.loads((out / "mini_walk_builtin_6pt.score.json").read_text())
    assert doc["readout"] == "openxr-screen" and doc["run"]["frames"] == 3


def test_score_off_the_screen_matches_the_in_process_harness(xr_run, tmp_path):
    _, report, _ = xr_run
    # The same solver fed directly from the test file, read through the same recorder shader.
    direct, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="builtin", out_dir=tmp_path, readout="shadermotion-gpu",
                         calibration="tpose")
    assert report.frames_scored == 3 and report.frames_missing == 0
    assert report.body_score_deg == pytest.approx(direct.body_score_deg, abs=1.5)
    # Head and hips are tracked directly, so they must come back exact through the whole chain.
    assert report.bones["Head"].angle_mean_deg < 0.5 and report.bones["Hips"].angle_mean_deg < 0.5


def test_recording_of_the_screen_gives_the_same_poses(xr_run):
    _, report, info = xr_run
    if "video" not in info:
        pytest.skip("needs ffmpeg")
    v = info["video"]
    assert v["video_frames"] >= 6 and Path(v["path"]).stat().st_size > 1000
    assert v["difference_from_screen"]["frames"] == 3
    assert v["difference_from_screen"]["max_deg"] < 2.0 and v["difference_from_screen"]["mean_deg"] < 0.5
    assert v["body_score_deg"] == pytest.approx(report.body_score_deg, abs=0.3)


def test_wrong_trackers_score_worse_through_openxr(xr_run, tmp_path):
    _, report, _ = xr_run
    # This run also covers the fallback: body trackers from the driver's state query.
    bad, info = run(DATA / "mini_walk.json", "6pt", "builtin", tmp_path, port=PORT, perturb="hands_swap:1",
                    trackers="driver", log=lambda *_: None)
    assert info["perturbation"] == "hands_swap:1"
    assert set(info["body_source"].values()) == {"driver"}
    assert bad.body_score_deg > report.body_score_deg + 5.0
