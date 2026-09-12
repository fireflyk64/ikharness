"""Unit tests for datasets, tracker placement, test files and scoring (no Godot needed)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from ikharness.dataset import BODY_BONES, Dataset, Frame
from ikharness.mathutil import Transform, quat_angle, quat_from_axis_angle, quat_mul, quat_rotate
from ikharness.scoring import score
from ikharness.testfile import HarnessResult, build_test_file
from ikharness.trackers import TRACKER_SETS, from_openxr_stage, place_trackers, rules_for, to_openxr_stage

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return Dataset.load(DATA / "mini_walk.json")


def test_transform_algebra():
    a = Transform((1, 2, 3), quat_from_axis_angle((0, 1, 0), math.radians(90)))
    b = Transform((0.5, 0, 0), quat_from_axis_angle((1, 0, 0), math.radians(30)))
    ab = a * b
    p = np.array([0.1, 0.2, 0.3])
    assert np.allclose(ab.apply(p), a.apply(b.apply(p)))
    ident = a * a.inverse()
    assert np.allclose(ident.position, 0, atol=1e-9)
    assert quat_angle(ident.rotation, np.array([0, 0, 0, 1.0])) < 1e-9
    assert abs(quat_angle(a.rotation, b.rotation) - quat_angle(b.rotation, a.rotation)) < 1e-12


def test_dataset_roundtrip(tmp_path, dataset):
    assert dataset.skeleton.has("Hips") and dataset.skeleton.has("LeftHand")
    assert len(dataset.frames) == 3
    assert abs(dataset.skeleton.hips_height - 0.85) < 1e-3
    lengths = dataset.skeleton.limb_lengths()
    assert 0.2 < lengths["LeftUpperArm"] < 0.4 and abs(lengths["LeftUpperArm"] - lengths["RightUpperArm"]) < 1e-4
    out = tmp_path / "copy.json"
    dataset.save(out)
    again = Dataset.load(out)
    assert again.skeleton.order == dataset.skeleton.order
    for f1, f2 in zip(dataset.frames, again.frames):
        for b in f1.bones:
            assert np.allclose(f1.bones[b].position, f2.bones[b].position)
    merged = dataset.merge(again)
    assert len(merged.frames) == 6 and merged.frames[-1].source == dataset.frames[-1].source + len(dataset.sources)


def test_rest_delta_identity_on_tpose(dataset):
    """A frame equal to the rest pose has zero delta rotation for every bone."""
    rest = Frame(0, 0.0, {n: b.rest_global for n, b in dataset.skeleton.bones.items()})
    for b in dataset.body_bones():
        assert quat_angle(rest.delta_from_rest(dataset.skeleton, b), np.array([0, 0, 0, 1.0])) < 1e-6


def test_tracker_placement_and_space_conversion(dataset):
    sk = dataset.skeleton
    rules = rules_for(sk)
    # The head tracker sits at the eye midpoint in the rest pose.
    rest = Frame(0, 0.0, {n: b.rest_global for n, b in sk.bones.items()})
    head = place_trackers(rest, sk, ["head"], rules)["head"]
    assert abs(head.position[1] - sk.eye_height) < 1e-6
    # Feet trackers coincide with the foot bones.
    tr = place_trackers(dataset.frames[1], sk, TRACKER_SETS["6pt"], rules)
    assert np.allclose(tr["left_foot"].position, dataset.frames[1].bones["LeftFoot"].position)
    # Stage conversion is a yaw of 180 degrees and its own inverse.
    s = to_openxr_stage(tr["waist"])
    assert np.allclose(s.position, tr["waist"].position * np.array([-1, 1, -1]))
    back = from_openxr_stage(s)
    assert np.allclose(back.position, tr["waist"].position) and quat_angle(back.rotation, tr["waist"].rotation) < 1e-9


def test_test_file_and_result_file(tmp_path, dataset):
    path = tmp_path / "t.json"
    doc = build_test_file(dataset, "6pt", path, dataset_path="x.json")
    assert doc["roles"] == TRACKER_SETS["6pt"]
    assert set(doc["rules"]) == set(TRACKER_SETS["6pt"])
    assert doc["rules"]["chest"]["bone"] if "chest" in doc["rules"] else True
    loaded = json.loads(path.read_text())
    assert len(loaded["frames"]) == 3 and "trackers" in loaded["frames"][0]
    # A result file that echoes the reference scores zero.
    res = {
        "format": "ikharness-result/1", "implementation": "echo", "tracker_set": "6pt",
        "frames": [{"index": i, "bones": f.to_dict()["bones"]} for i, f in enumerate(dataset.frames)],
    }
    rpath = tmp_path / "r.json"
    rpath.write_text(json.dumps(res))
    result = HarnessResult.load(rpath)
    report = score(dataset, result.frames, implementation=result.implementation, tracker_set="6pt")
    assert report.body_score_deg < 1e-5 and report.end_effector_position_mean_m < 1e-9
    assert report.frames_scored == 3 and report.frames_missing == 0


def test_scoring_detects_rotation_and_missing(dataset):
    q = quat_from_axis_angle((1, 0, 0), math.radians(10))
    perturbed = []
    for f in dataset.frames:
        perturbed.append(Frame(f.source, f.time, {b: Transform(t.position, quat_mul(q, t.rotation)) for b, t in f.bones.items()}))
    perturbed[-1] = None
    report = score(dataset, perturbed, implementation="p", tracker_set="6pt")
    assert abs(report.body_score_deg - 10.0) < 1e-6
    assert report.frames_missing == 1 and report.frames_scored == 2
    assert all(abs(s.angle_max_deg - 10.0) < 1e-6 for s in report.bones.values())
    assert "body score 10.00 deg" in report.summary()
