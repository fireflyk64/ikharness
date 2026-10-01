"""T-pose calibration: the reference math must recover bone targets under any root and tracker mounting."""

import math
from pathlib import Path

import numpy as np
import pytest

from ikharness.calibration import calibrate, role_bone, solve_root, tpose_trackers
from ikharness.dataset import Dataset
from ikharness.mathutil import Transform, quat_angle, quat_from_axis_angle
from ikharness.negative import make_tracker_perturbation
from ikharness.trackers import TRACKER_SETS, place_trackers, rules_for, to_openxr_device

DATA = Path(__file__).parent / "data"
ROLES = TRACKER_SETS["11pt"]


@pytest.fixture(scope="module")
def dataset():
    return Dataset.load(DATA / "mini_walk.json")


def close(a: Transform, b: Transform, pos=1e-6, ang=1e-5):
    # Dataset values come from float32 engine math, so exact means ~1e-7.
    return np.linalg.norm(a.position - b.position) < pos and quat_angle(a.rotation, b.rotation) < ang


def test_identity_setup_recovers_rule_offsets(dataset):
    sk = dataset.skeleton
    cal = calibrate(sk, tpose_trackers(sk, ROLES))
    assert np.linalg.norm(cal.root.position) < 1e-6 and quat_angle(cal.root.rotation, np.array([0, 0, 0, 1.0])) < 1e-5
    assert abs(cal.height_ratio - 1.0) < 1e-6
    rules = rules_for(sk)
    for role in ROLES:
        assert close(cal.offsets[role], Transform(rules[role].offset, rules[role].rotation)), role
    # Bone targets for a real frame equal the reference bones.
    f = dataset.frames[1]
    tr = place_trackers(f, sk, ROLES, rules)
    for role in ROLES:
        assert close(cal.bone_target(role, tr[role]), f.bones[role_bone(sk, role)]), role


def test_user_anywhere_with_strapped_trackers(dataset):
    """The user stands elsewhere, turned, with body trackers mounted arbitrarily: targets are still exact."""
    sk = dataset.skeleton
    world = Transform((1.7, 0.0, -2.3), quat_from_axis_angle((0, 1, 0), math.radians(137)))
    mount = make_tracker_perturbation("tracker_mount:0.08:5")
    tpose = {r: world * t for r, t in mount(tpose_trackers(sk, ROLES), -1).items()}
    cal = calibrate(sk, tpose)
    assert np.linalg.norm(cal.root.position - world.position) < 1e-6
    assert quat_angle(cal.root.rotation, world.rotation) < 1e-5
    for f in dataset.frames:
        tr = {r: world * t for r, t in mount(place_trackers(f, sk, ROLES), 0).items()}
        for role in ROLES:
            assert close(cal.bone_target(role, tr[role]), f.bones[role_bone(sk, role)]), role


def test_openxr_device_convention(dataset):
    """Through the OpenXR stage conversion the avatar root comes out turned by 180 degrees and targets stay exact."""
    sk = dataset.skeleton
    tpose = {r: to_openxr_device(r, t) for r, t in tpose_trackers(sk, ROLES).items()}
    cal = calibrate(sk, tpose)
    assert abs(abs(math.degrees(quat_angle(cal.root.rotation, np.array([0, 0, 0, 1.0])))) - 180.0) < 1e-4
    # The headset looks along its own -Z where the avatar faces (stage -Z in rest).
    from ikharness.mathutil import quat_rotate
    assert np.allclose(quat_rotate(tpose["head"].rotation, np.array([0, 0, -1.0])), (0, 0, -1), atol=1e-6)
    f = dataset.frames[2]
    tr = {r: to_openxr_device(r, t) for r, t in place_trackers(f, sk, ROLES).items()}
    for role in ROLES:
        assert close(cal.bone_target(role, tr[role]), f.bones[role_bone(sk, role)]), role


def test_root_from_feet_or_head_without_hands(dataset):
    sk = dataset.skeleton
    world = Transform((0.4, 0.0, 0.9), quat_from_axis_angle((0, 1, 0), math.radians(-60)))
    t = {r: world * v for r, v in tpose_trackers(sk, ["head", "waist", "left_foot", "right_foot"]).items()}
    assert quat_angle(solve_root(sk, t).rotation, world.rotation) < 1e-5
    # Head only: fall back to the headset's -Z view direction (OpenXR device convention).
    head = {"head": world * to_openxr_device("head", tpose_trackers(sk, ["head"])["head"])}
    yaw = solve_root(sk, head).rotation
    expect = quat_from_axis_angle((0, 1, 0), math.radians(-60 + 180))
    assert quat_angle(yaw, expect) < 1e-5
