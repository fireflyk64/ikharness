"""Choose frames by pose diversity instead of uniform time steps.

    ikh dataset select --dataset big.json --count 40 --out small.json [--method diversity|uniform]
    ikh dataset build ... --frames 40 --select diversity [--oversample 6]

Uniform sampling of a walk cycle returns the same few poses over and over, and a long idle
stretch in a dance clip crowds out the hard moments. Selection here is farthest point
sampling in pose space: start from the pose nearest to the average, then keep adding the
candidate whose nearest already-chosen pose is farthest away. The distance between two
poses is the mean angle between corresponding body bones, after removing each pose's
heading (yaw of the hips) and ignoring where the avatar stands, since neither changes what
an IK solver has to do.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

from .dataset import BODY_BONES, Dataset, Frame, Skeleton
from .mathutil import quat_from_axis_angle, quat_inv, quat_mul, quat_rotate


def heading(frame: Frame) -> float:
    """Yaw (radians about +Y) of the hips' forward axis; 0 when the avatar faces +Z."""
    fwd = quat_rotate(frame.bones["Hips"].rotation, np.array([0.0, 0.0, 1.0]))
    if math.hypot(fwd[0], fwd[2]) < 1e-6:
        return 0.0
    return math.atan2(fwd[0], fwd[2])


def pose_features(frame: Frame, skeleton: Skeleton, bones: Optional[Sequence[str]] = None) -> np.ndarray:
    """(bones, 4) unit quaternions: each bone's rotation away from rest with the heading removed."""
    names = [b for b in (bones or BODY_BONES) if skeleton.has(b) and b in frame.bones]
    unyaw = quat_from_axis_angle([0.0, 1.0, 0.0], -heading(frame))
    out = np.zeros((len(names), 4))
    for i, b in enumerate(names):
        q = quat_mul(unyaw, frame.delta_from_rest(skeleton, b))
        out[i] = q if q[3] >= 0 else -q
    return out


def distance_matrix(features: np.ndarray) -> np.ndarray:
    """Mean per-bone angle (degrees) between every pair of poses; features is (frames, bones, 4)."""
    n = features.shape[0]
    flat = features.reshape(n, -1, 4)
    out = np.zeros((n, n))
    for i in range(n):
        dots = np.abs(np.einsum("bk,nbk->nb", flat[i], flat)).clip(0.0, 1.0)
        out[i] = np.degrees(2.0 * np.arccos(dots)).mean(axis=1)
    return out


def farthest_point_order(dist: np.ndarray, count: int) -> List[int]:
    n = dist.shape[0]
    count = min(count, n)
    if count <= 0:
        return []
    first = int(dist.mean(axis=1).argmin())      # the most ordinary pose
    chosen = [first]
    nearest = dist[first].copy()
    while len(chosen) < count:
        nxt = int(nearest.argmax())
        if nearest[nxt] <= 0.0:                   # only duplicates left
            remaining = [i for i in range(n) if i not in set(chosen)]
            chosen += remaining[: count - len(chosen)]
            break
        chosen.append(nxt)
        nearest = np.minimum(nearest, dist[nxt])
    return chosen


def uniform_indices(n: int, count: int) -> List[int]:
    count = min(count, n)
    return sorted({int(round(i * (n - 1) / max(count - 1, 1))) for i in range(count)}) if count else []


@dataclass
class Selection:
    indices: List[int]                # into the candidate frames, in chronological order
    spread_deg: float                 # mean distance of a chosen pose to its nearest chosen neighbour
    coverage_deg: float               # largest distance of any candidate to its nearest chosen pose


def measure(dist: np.ndarray, indices: Sequence[int]) -> Selection:
    idx = list(indices)
    sub = dist[np.ix_(idx, idx)].copy()
    np.fill_diagonal(sub, np.inf)
    spread = float(sub.min(axis=1).mean()) if len(idx) > 1 else 0.0
    coverage = float(dist[:, idx].min(axis=1).max()) if idx else 0.0
    return Selection(sorted(idx), spread, coverage)


def select(dataset: Dataset, count: int, method: str = "diversity", bones: Optional[Sequence[str]] = None) -> Selection:
    features = np.stack([pose_features(f, dataset.skeleton, bones) for f in dataset.frames])
    dist = distance_matrix(features)
    if method == "diversity":
        return measure(dist, farthest_point_order(dist, count))
    if method == "uniform":
        return measure(dist, uniform_indices(len(dataset.frames), count))
    raise ValueError(f"unknown selection method {method!r} (diversity, uniform)")


def subset(dataset: Dataset, indices: Sequence[int], note: str = "") -> Dataset:
    generator = dict(dataset.generator)
    if note:
        generator["selection"] = note
    return Dataset(skeleton=dataset.skeleton, frames=[dataset.frames[i] for i in indices], sources=dataset.sources,
                   generator=generator)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ikh dataset select", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True, help="dataset with the candidate frames")
    p.add_argument("--count", type=int, required=True, help="frames to keep")
    p.add_argument("--method", default="diversity", choices=["diversity", "uniform"])
    p.add_argument("--out", default=None, help="write the selected frames as a new dataset")
    args = p.parse_args(argv)
    dataset = Dataset.load(args.dataset)
    for method in ("uniform", "diversity"):
        s = select(dataset, args.count, method)
        mark = "*" if method == args.method else " "
        print(f"{mark} {method:<9} {len(s.indices)} of {len(dataset.frames)} frames: nearest-neighbour spread {s.spread_deg:.1f} deg, "
              f"worst uncovered pose {s.coverage_deg:.1f} deg away")
    chosen = select(dataset, args.count, args.method)
    if args.out:
        subset(dataset, chosen.indices, f"{args.method}: {len(chosen.indices)} of {len(dataset.frames)}").save(args.out)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
