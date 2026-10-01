"""Frame selection by pose diversity (pure Python)."""

import math
from pathlib import Path

import numpy as np

from ikharness.dataset import Dataset, Frame
from ikharness.mathutil import Transform, quat_from_axis_angle, quat_mul
from ikharness.select import distance_matrix, farthest_point_order, heading, measure, pose_features, select, subset, uniform_indices

DATA = Path(__file__).parent / "data"


def posed(dataset, bone, axis, deg, yaw_deg=0.0, shift=(0.0, 0.0, 0.0)):
    """The rest pose with one bone rotated, the whole avatar turned by yaw and moved by shift."""
    sk = dataset.skeleton
    turn = quat_from_axis_angle([0, 1, 0], math.radians(yaw_deg))
    bend = quat_from_axis_angle(axis, math.radians(deg))
    bones = {}
    for name in sk.order:
        rest = sk.bones[name].rest_global
        rot = quat_mul(bend, rest.rotation) if name == bone else rest.rotation
        bones[name] = Transform(rest.position + np.array(shift), quat_mul(turn, rot))
    return Frame(source=0, time=0.0, bones=bones)


def test_heading_and_position_do_not_count():
    d = Dataset.load(DATA / "mini_walk.json")
    a = posed(d, "LeftUpperArm", [0, 0, 1], 40)
    b = posed(d, "LeftUpperArm", [0, 0, 1], 40, yaw_deg=135, shift=(2.0, 0.0, -1.0))
    assert abs(heading(b) - math.radians(135)) < 1e-6
    feats = np.stack([pose_features(f, d.skeleton) for f in (a, b)])
    assert distance_matrix(feats)[0, 1] < 1e-4


def test_distance_is_the_mean_bone_angle():
    d = Dataset.load(DATA / "mini_walk.json")
    rest = posed(d, "Hips", [0, 1, 0], 0)
    bent = posed(d, "LeftLowerArm", [1, 0, 0], 60)
    feats = np.stack([pose_features(f, d.skeleton) for f in (rest, bent)])
    # One of the body bones differs by 60 degrees, the others by nothing.
    assert abs(distance_matrix(feats)[0, 1] * feats.shape[1] - 60.0) < 1e-3


def test_farthest_points_cover_clusters_that_uniform_sampling_misses():
    d = Dataset.load(DATA / "mini_walk.json")
    # 18 near-identical idle frames, then three distinct poses at the very end.
    frames = [posed(d, "Spine", [1, 0, 0], 0.2 * i) for i in range(18)]
    frames += [posed(d, "LeftUpperArm", [0, 0, 1], 80), posed(d, "RightUpperLeg", [1, 0, 0], -70), posed(d, "Head", [0, 1, 0], 60)]
    ds = Dataset(skeleton=d.skeleton, frames=frames, sources=d.sources, generator={})
    diverse = select(ds, 4, "diversity")
    uniform = select(ds, 4, "uniform")
    assert {18, 19, 20} <= set(diverse.indices)
    assert diverse.indices == sorted(diverse.indices)
    assert diverse.coverage_deg < 1.0 < uniform.coverage_deg
    assert diverse.spread_deg > uniform.spread_deg
    small = subset(ds, diverse.indices, "diversity: 4 of 21")
    assert len(small.frames) == 4 and small.generator["selection"].startswith("diversity")


def test_orders_and_edge_cases():
    dist = np.array([[0, 1, 5], [1, 0, 4], [5, 4, 0]], dtype=float)
    assert farthest_point_order(dist, 2) == [1, 2]          # starts at the most central pose
    assert farthest_point_order(dist, 10) == [1, 2, 0]
    assert farthest_point_order(np.zeros((3, 3)), 2) == [0, 1]   # duplicates only
    assert uniform_indices(10, 4) == [0, 3, 6, 9] and uniform_indices(3, 5) == [0, 1, 2]
    assert measure(dist, [0, 2]).spread_deg == 5.0 and measure(dist, [0, 2]).coverage_deg == 1.0
