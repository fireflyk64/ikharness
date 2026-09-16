"""The metric must get worse under perturbation (no Godot needed)."""

from pathlib import Path

import pytest

from ikharness.dataset import Dataset
from ikharness.negative import DEFAULT_FRAME_LADDERS, LadderStep, is_monotonic, make_tracker_perturbation, perturb_frames
from ikharness.scoring import score
from ikharness.testfile import build_test_file
from ikharness.trackers import TRACKER_SETS, place_trackers, rules_for

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def dataset():
    return Dataset.load(DATA / "mini_walk.json")


@pytest.mark.parametrize("name,ladder", sorted(DEFAULT_FRAME_LADDERS.items()))
def test_frame_perturbation_ladders_are_monotonic(dataset, name, ladder):
    steps = [LadderStep("none", score(dataset, dataset.frames).weighted_score_deg, 0.0)]
    for spec in ladder:
        rep = score(dataset, perturb_frames(dataset.frames, spec))
        steps.append(LadderStep(spec, rep.weighted_score_deg, rep.end_effector_position_mean_m))
    assert steps[0].score_deg < 1e-6
    assert is_monotonic(steps), [(s.spec, round(s.score_deg, 2)) for s in steps]
    assert steps[-1].score_deg > steps[1].score_deg + 1.0


def test_rotate_arms_only_touches_arms(dataset):
    rep = score(dataset, perturb_frames(dataset.frames, "rotate_arms:20"))
    assert abs(rep.bones["LeftLowerArm"].angle_mean_deg - 20.0) < 1e-6
    assert rep.bones["LeftUpperLeg"].angle_mean_deg < 1e-6
    assert rep.bones["Hips"].angle_mean_deg < 1e-6


def test_drop_frames_counts_missing(dataset):
    rep = score(dataset, perturb_frames(dataset.frames, "drop_frames:0.34"))
    assert rep.frames_missing == 1 and rep.frames_scored == 2


def test_tracker_perturbations_change_only_their_roles(dataset, tmp_path):
    sk = dataset.skeleton
    rules = rules_for(sk)
    base = place_trackers(dataset.frames[0], sk, TRACKER_SETS["6pt"], rules)
    moved = make_tracker_perturbation("hands_offset:0.1")(base, 0)
    assert abs(moved["left_hand"].position[2] - base["left_hand"].position[2] - 0.1) < 1e-9
    assert (moved["head"].position == base["head"].position).all()
    swapped = make_tracker_perturbation("hands_swap")(base, 0)
    assert (swapped["left_hand"].position == base["right_hand"].position).all()
    doc = build_test_file(dataset, "6pt", tmp_path / "t.json", perturb=make_tracker_perturbation("head_yaw:30"),
                          perturb_name="head_yaw:30")
    assert doc["perturbation"] == "head_yaw:30"
    with pytest.raises(KeyError):
        make_tracker_perturbation("bogus:1")


def test_rest_relative_scoring_ignores_bone_axes(dataset):
    """Rotating every rest and pose by the same per-bone rotation leaves the score at 0."""
    import numpy as np
    from ikharness.dataset import Frame
    from ikharness.mathutil import Transform, quat_from_axis_angle, quat_mul

    rng = np.random.default_rng(1)
    rest = {}
    frames = []
    twist = {b: quat_from_axis_angle(rng.normal(size=3), rng.uniform(0, 3.0)) for b in dataset.skeleton.order}
    for b in dataset.skeleton.order:
        r = dataset.skeleton.bones[b].rest_global
        rest[b] = Transform(r.position, quat_mul(r.rotation, twist[b]))
    for f in dataset.frames:
        frames.append(Frame(f.source, f.time, {b: Transform(t.position, quat_mul(t.rotation, twist[b])) for b, t in f.bones.items()}))
    rep = score(dataset, frames, result_rest=rest)
    assert rep.body_score_deg < 1e-5
    # Without the result rest the same data looks badly wrong.
    assert score(dataset, frames).body_score_deg > 20.0
