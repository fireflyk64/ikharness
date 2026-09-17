"""ShaderMotion pose layer: rotations <-> swing-twist slots, whole frames, the genuine frame."""

import math
from pathlib import Path

import numpy as np
import pytest

from ikharness.dataset import Dataset, Frame
from ikharness.mathutil import quat_angle, quat_from_axis_angle
from ikharness.scoring import score
from ikharness.shadermotion import codec as C
from ikharness.shadermotion import humanoid as H

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def dataset():
    return Dataset.load(DATA / "mini_walk.json")


def test_swing_twist_round_trip():
    rng = np.random.default_rng(0)
    for _ in range(500):
        v = np.array([rng.uniform(-3.0, 3.0), *rng.uniform(-1.5, 1.5, size=2)])
        back = H.swing_twist_inv(H.swing_twist(v))
        assert np.allclose(back, v, atol=1e-7), (v, back)
    assert np.allclose(H.swing_twist((0, 0, 0)), (0, 0, 0, 1))


def test_bone_round_trip_is_exact_for_representable_rotations():
    rng = np.random.default_rng(1)
    idx = H.bone_index("LeftUpperArm")  # three muscle axes
    for _ in range(100):
        slots = rng.uniform(-0.6, 0.6, size=3)
        q = H.decode_bone(idx, slots)
        back, deg, _ = H.encode_bone(idx, q)
        assert deg < 1e-4 and np.allclose(back, slots, atol=1e-6)


def test_hinge_reports_projection_residual():
    idx = H.bone_index("LeftLowerArm")  # no swing about Y
    q = H.decode_bone(idx, (0.1, 0.0, -0.3))
    twisted = H.quat_mul(q, quat_from_axis_angle((0, 0, 1), math.radians(20)))
    _, deg_ok, _ = H.encode_bone(idx, q)
    _, deg_bad, _ = H.encode_bone(idx, twisted)
    assert deg_ok < 1e-4 and deg_bad > 1.0


def test_dataset_frames_survive_shadermotion(dataset):
    """Frame -> slots -> image (8 bit) -> slots -> frame: the measurement floor of the format."""
    results = []
    worst_residual = 0.0
    for f in dataset.frames:
        slots, residuals = H.frame_to_slots(f, dataset.skeleton)
        worst_residual = max(worst_residual, max(residuals.values()))
        img = C.encode_frame(slots, 1280, 720)
        results.append(H.slots_to_frame(C.decode_frame(img), dataset.skeleton, f.time))
    rep = score(dataset, results, implementation="shadermotion-roundtrip", tracker_set="-")
    hips = rep.bones["Hips"]
    assert hips.angle_mean_deg < 0.2 and hips.position_mean_m < 0.002
    assert rep.body_score_deg < 3.0, rep.summary()
    assert rep.end_effector_position_mean_m < 0.03, rep.summary()
    print(rep.summary())
    print("worst single-bone projection residual: %.2f deg" % worst_residual)


def test_rest_pose_round_trips(dataset):
    sk = dataset.skeleton
    rest = Frame(0, 0.0, {n: b.rest_global for n, b in sk.bones.items()})
    slots, residuals = H.frame_to_slots(rest, sk)
    back = H.slots_to_frame(C.decode_frame(C.encode_frame(slots, 1280, 720)), sk)
    # Not exact: the pre/post rotation tables were exported from one particular avatar, so a
    # perfectly straight profile leg sits 1.5 degrees off that avatar's knee hinge plane.
    for b in dataset.body_bones():
        assert math.degrees(quat_angle(back.bones[b].rotation, rest.bones[b].rotation)) < 2.5, (b, residuals.get(b))
    # In Mecanim's swing-twist space the neutral pose is the "motorcycle" pose, so a straight
    # limb is not zero: knees and elbows read +80 degrees in T-pose, upper legs +30.
    angles = {b: np.array(C.slots_to_angles(b, np.array([slots.get(i, 0.0) for i in range(C.SLOT_COLS * C.SLOT_ROWS)]))) for b in ("LeftLowerLeg", "LeftLowerArm", "LeftUpperLeg", "Spine")}
    assert abs(angles["LeftLowerLeg"][2] - 80.0) < 1.0 and abs(angles["LeftLowerArm"][2] - 80.0) < 1.0
    assert abs(angles["LeftUpperLeg"][2] - 30.0) < 1.0 and np.allclose(angles["Spine"], 0.0, atol=0.1)


def test_genuine_frame_is_a_plausible_human(dataset):
    """The Unity-encoded frame is a standing VR user with both hands raised in front of the chest."""
    from PIL import Image

    from ikharness.mathutil import quat_rotate
    img = np.array(Image.open(DATA / "shadermotion/upstream_frame.png").convert("RGB"))
    f = H.slots_to_frame(C.decode_frame(img, grid_w=6), dataset.skeleton)
    hips = f.bones["Hips"]
    fwd = quat_rotate(hips.rotation, np.array([0.0, 0.0, 1.0]))
    left = quat_rotate(hips.rotation, np.array([1.0, 0.0, 0.0]))
    rel = {b: t.position - hips.position for b, t in f.bones.items()}

    def flexion(a, b, c):
        u = f.bones[b].position - f.bones[a].position
        v = f.bones[c].position - f.bones[b].position
        return math.degrees(math.acos(np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v))))

    assert rel["Head"][1] > 0.5                                  # upright
    assert rel["LeftFoot"][1] < -0.6 and rel["RightFoot"][1] < -0.6   # standing on straight legs
    assert flexion("LeftUpperLeg", "LeftLowerLeg", "LeftFoot") < 15.0
    assert flexion("RightUpperLeg", "RightLowerLeg", "RightFoot") < 15.0
    # Left limbs are on the character's left, right limbs on the right (handedness).
    assert np.dot(rel["LeftFoot"], left) > 0.05 > -0.05 > np.dot(rel["RightFoot"], left)
    assert np.dot(rel["LeftHand"], left) > 0.05 and np.dot(rel["RightHand"], left) < -0.05
    # Elbows strongly flexed, elbows behind the torso line and hands in front of it.
    assert flexion("LeftUpperArm", "LeftLowerArm", "LeftHand") > 100.0
    assert flexion("RightUpperArm", "RightLowerArm", "RightHand") > 100.0
    for side in ("Left", "Right"):
        assert np.dot(rel[f"{side}Hand"], fwd) > np.dot(rel[f"{side}LowerArm"], fwd) + 0.1
