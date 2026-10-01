"""Integration test: the Godot harness solves the mini dataset with RenIK (skips without Godot)."""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from ikharness.dataset import Dataset
from ikharness.run_godot import evaluate

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def godot():
    exe = os.environ.get("GODOT") or shutil.which("godot") or str(Path.home() / ".local/bin/godot")
    if not Path(exe).exists():
        pytest.skip("Godot 4 binary not found (set GODOT)")
    os.environ["GODOT"] = exe
    return exe


def test_renik_reaches_targets(godot, tmp_path):
    report, _log = evaluate(DATA / "mini_walk.json", "6pt", ik="renik", settle=6, out_dir=tmp_path)
    assert report.frames_scored == 3 and report.frames_missing == 0
    # Hands, feet, hips and head are driven directly by trackers placed on the bones.
    for bone in ("Hips", "Head", "LeftFoot", "RightFoot"):
        assert report.bones[bone].angle_mean_deg < 0.1, bone
    for bone in ("LeftHand", "RightHand"):
        assert report.bones[bone].position_mean_m < 0.03, bone
    assert report.body_score_deg < 25.0


def test_renik_uses_elbow_knee_and_chest_trackers(godot, tmp_path):
    """11 points must beat 6 points, with hands still on target.

    Regressions this guards: the arm pole direction lying along the elbow's hinge axis
    (undefined bend plane), and the chest target's position dragging the head target
    (shoulders 16 cm off, hands 3.8 cm short on the mocap set).
    """
    six, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="renik", settle=6, out_dir=tmp_path)
    eleven, _ = evaluate(DATA / "mini_walk.json", "11pt", ik="renik", settle=6, out_dir=tmp_path)
    assert eleven.body_score_deg < six.body_score_deg - 1.0
    assert eleven.end_effector_position_mean_m < 0.01
    for bone in ("LeftLowerLeg", "RightLowerLeg"):
        assert eleven.bones[bone].angle_mean_deg < 8.0, bone
    for bone in ("LeftUpperArm", "RightUpperArm"):
        assert eleven.bones[bone].angle_mean_deg < six.bones[bone].angle_mean_deg, bone
        assert eleven.bones[bone].position_mean_m < 0.05, bone


def test_no_ik_baseline_is_worse(godot, tmp_path):
    ref, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="none", settle=2, out_dir=tmp_path)
    ik, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="renik", settle=6, out_dir=tmp_path)
    assert ik.body_score_deg < ref.body_score_deg
    assert ik.end_effector_position_mean_m < ref.end_effector_position_mean_m


def test_builtin_adapter_reaches_targets(godot, tmp_path):
    report, _log = evaluate(DATA / "mini_walk.json", "6pt", ik="builtin", settle=6, out_dir=tmp_path)
    assert report.frames_scored == 3 and report.frames_missing == 0
    for bone in ("Hips", "Head", "LeftHand", "RightHand", "LeftFoot", "RightFoot"):
        assert report.bones[bone].angle_mean_deg < 0.1, bone
        assert report.bones[bone].position_mean_m < 0.02, bone
    assert report.body_score_deg < 25.0


def test_input_perturbation_lowers_score(godot, tmp_path):
    base, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="builtin", settle=6, out_dir=tmp_path)
    worse, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="builtin", settle=6, out_dir=tmp_path, perturb="feet_offset:0.2")
    assert worse.weighted_score_deg > base.weighted_score_deg + 1.0


def test_shadermotion_pixel_readout_matches_python_encoder(godot, tmp_path):
    """Godot's CPU ShaderMotion encoder and the Python encoder must agree on the same solved poses."""
    import math

    from ikharness.mathutil import quat_angle
    from ikharness.shadermotion.readout import decode_directory, roundtrip_frames
    from ikharness.testfile import HarnessResult

    report, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="builtin", settle=6, out_dir=tmp_path, readout="shadermotion")
    assert report.frames_scored == 3 and report.frames_missing == 0
    dataset = Dataset.load(DATA / "mini_walk.json")
    solved = HarnessResult.load(tmp_path / "mini_walk_builtin_6pt.result.json")
    from_godot = decode_directory(tmp_path / "mini_walk_builtin_6pt.shadermotion", dataset.skeleton, count=3)
    from_python = roundtrip_frames(solved.frames, dataset.skeleton)
    for a, b in zip(from_godot, from_python):
        for bone in dataset.body_bones():
            assert math.degrees(quat_angle(a.bones[bone].rotation, b.bones[bone].rotation)) < 0.1, bone
    # Reading through pixels costs accuracy but stays in the same league as the direct readout.
    assert report.weighted_score_deg < report.json_readout.weighted_score_deg + 8.0


def test_tpose_calibration_matches_rules_and_absorbs_mounting(godot, tmp_path):
    """Deriving offsets from the T-pose frame equals being told them, and absorbs strapped-on trackers."""
    args = dict(ik="builtin", settle=6, out_dir=tmp_path)
    rules, _ = evaluate(DATA / "mini_walk.json", "11pt", **args)
    tpose, log = evaluate(DATA / "mini_walk.json", "11pt", calibration="tpose", **args)
    assert "T-pose calibration, root yaw" in log
    assert abs(tpose.weighted_score_deg - rules.weighted_score_deg) < 0.01
    mounted_rules, _ = evaluate(DATA / "mini_walk.json", "11pt", perturb="tracker_mount:0.08", **args)
    mounted_tpose, _ = evaluate(DATA / "mini_walk.json", "11pt", perturb="tracker_mount:0.08", calibration="tpose", **args)
    assert mounted_rules.weighted_score_deg > rules.weighted_score_deg + 2.0   # uncalibrated mounting hurts
    assert abs(mounted_tpose.weighted_score_deg - rules.weighted_score_deg) < 0.01   # calibration removes it


def test_renik_places_untracked_feet_on_the_floor(godot, tmp_path, monkeypatch):
    """3 points: RenIKPlacement3D raycasts the feet onto the floor and puts the hips over them."""
    from ikharness.testfile import HarnessResult
    dataset = Dataset.load(DATA / "mini_walk.json")
    report, _ = evaluate(DATA / "mini_walk.json", "3pt", ik="renik", settle=6, out_dir=tmp_path / "placed")
    placed = HarnessResult.load(tmp_path / "placed" / "mini_walk_renik_3pt.result.json")
    assert report.frames_scored == 3
    for frame, ref in zip(placed.frames, dataset.frames):
        for foot in ("LeftFoot", "RightFoot"):
            assert 0.02 < frame.bones[foot].position[1] < 0.25, foot          # ankle just above the floor
            head = frame.bones["Head"].position
            assert abs(frame.bones[foot].position[0] - head[0]) < 0.3 and abs(frame.bones[foot].position[2] - head[2]) < 0.3
        assert abs(frame.bones["Hips"].position[1] - ref.bones["Hips"].position[1]) < 0.1
    # Switched off, the legs keep their rest pose under the hips: feet do not follow the floor.
    monkeypatch.setenv("IKH_RENIK_PLACEMENT", "0")
    evaluate(DATA / "mini_walk.json", "3pt", ik="renik", settle=6, out_dir=tmp_path / "rest")
    rest = HarnessResult.load(tmp_path / "rest" / "mini_walk_renik_3pt.result.json")
    sk = dataset.skeleton
    leg = sk.bones["LeftFoot"].rest_global.position - sk.bones["Hips"].rest_global.position
    got = rest.frames[0].bones["LeftFoot"].position - rest.frames[0].bones["Hips"].position
    assert abs(float(np.linalg.norm(got)) - float(np.linalg.norm(leg))) < 1e-3
    assert any(abs(a.bones["LeftFoot"].position[1] - b.bones["LeftFoot"].position[1]) > 0.01 for a, b in zip(placed.frames, rest.frames))
