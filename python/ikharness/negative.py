"""Negative tests: perturbations that must make the score worse.

Two layers:

* ``perturb_frames`` changes *solved poses* (metric layer, no Godot needed): proves the
  scorer reacts to wrong rotations where they were introduced.
* ``make_tracker_perturbation`` changes the *tracker inputs* of a harness run (pipeline
  layer): proves that feeding an IK system wrong trackers lowers the score.

Perturbation specs are strings ``name:magnitude[:seed]``. Magnitudes are degrees for
rotations and meters for offsets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .dataset import Frame
from .mathutil import Transform, quat_from_axis_angle, quat_mul, quat_rotate
from .testfile import TrackerHook

ARM_BONES = ["LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
             "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand"]
LEG_BONES = ["LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes",
             "RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes"]


def parse_spec(spec: str) -> Tuple[str, float, int]:
    parts = spec.split(":")
    name = parts[0]
    magnitude = float(parts[1]) if len(parts) > 1 else 1.0
    seed = int(parts[2]) if len(parts) > 2 else 0
    return name, magnitude, seed


# -- metric layer -------------------------------------------------------------------

def _rotate_bones(frames: Sequence[Optional[Frame]], bones: Optional[List[str]], deg: float, rng: np.random.Generator,
                  random_axis: bool) -> List[Optional[Frame]]:
    out: List[Optional[Frame]] = []
    for f in frames:
        if f is None:
            out.append(None)
            continue
        new = {}
        for b, t in f.bones.items():
            if bones is not None and b not in bones:
                new[b] = t
                continue
            axis = rng.normal(size=3) if random_axis else np.array([0.0, 1.0, 0.0])
            angle = math.radians(deg) * (rng.uniform(-1, 1) if random_axis else 1.0)
            new[b] = Transform(t.position, quat_mul(quat_from_axis_angle(axis, angle), t.rotation))
        out.append(Frame(f.source, f.time, new))
    return out


FRAME_PERTURBATIONS: Dict[str, str] = {
    "rotate_all": "rotate every bone by MAG degrees about +Y",
    "rotate_arms": "rotate the arm bones by MAG degrees about +Y",
    "rotate_legs": "rotate the leg bones by MAG degrees about +Y",
    "jitter": "random rotation of every bone, uniform in [-MAG, MAG] degrees about a random axis",
    "drop_frames": "replace a fraction MAG (0..1) of frames with None (unsolved)",
}


def perturb_frames(frames: Sequence[Optional[Frame]], spec: str) -> List[Optional[Frame]]:
    name, mag, seed = parse_spec(spec)
    rng = np.random.default_rng(seed)
    if name == "rotate_all":
        return _rotate_bones(frames, None, mag, rng, False)
    if name == "rotate_arms":
        return _rotate_bones(frames, ARM_BONES, mag, rng, False)
    if name == "rotate_legs":
        return _rotate_bones(frames, LEG_BONES, mag, rng, False)
    if name == "jitter":
        return _rotate_bones(frames, None, mag, rng, True)
    if name == "drop_frames":
        out = list(frames)
        n = int(round(mag * len(out)))
        for i in rng.choice(len(out), size=min(n, len(out)), replace=False):
            out[i] = None
        return out
    raise KeyError(f"unknown frame perturbation {name!r}; known: {sorted(FRAME_PERTURBATIONS)}")


# -- pipeline layer (tracker inputs) -------------------------------------------------

TRACKER_PERTURBATIONS: Dict[str, str] = {
    "hands_offset": "move both hand trackers MAG meters forward (+Z in dataset space)",
    "hands_up": "move both hand trackers MAG meters up",
    "hands_swap": "swap left and right hand trackers (MAG ignored)",
    "head_yaw": "rotate the head tracker by MAG degrees about +Y",
    "head_offset": "move the head tracker MAG meters forward",
    "feet_offset": "lift both foot trackers by MAG meters",
    "waist_offset": "move the waist tracker MAG meters sideways (+X)",
    "noise": "gaussian position noise with sigma MAG meters on every tracker",
    "rot_noise": "random rotation noise with sigma MAG degrees on every tracker",
}


def make_tracker_perturbation(spec: str) -> TrackerHook:
    name, mag, seed = parse_spec(spec)
    if name not in TRACKER_PERTURBATIONS:
        raise KeyError(f"unknown tracker perturbation {name!r}; known: {sorted(TRACKER_PERTURBATIONS)}")
    rng = np.random.default_rng(seed)

    def shift(t: Transform, delta) -> Transform:
        return Transform(t.position + np.asarray(delta, dtype=float), t.rotation)

    def hook(trackers: Dict[str, Transform], index: int) -> Dict[str, Transform]:
        tr = dict(trackers)
        if name == "hands_offset":
            for r in ("left_hand", "right_hand"):
                if r in tr:
                    tr[r] = shift(tr[r], (0, 0, mag))
        elif name == "hands_up":
            for r in ("left_hand", "right_hand"):
                if r in tr:
                    tr[r] = shift(tr[r], (0, mag, 0))
        elif name == "hands_swap":
            if "left_hand" in tr and "right_hand" in tr:
                tr["left_hand"], tr["right_hand"] = tr["right_hand"], tr["left_hand"]
        elif name == "head_yaw":
            if "head" in tr:
                q = quat_from_axis_angle((0, 1, 0), math.radians(mag))
                tr["head"] = Transform(tr["head"].position, quat_mul(q, tr["head"].rotation))
        elif name == "head_offset":
            if "head" in tr:
                tr["head"] = shift(tr["head"], (0, 0, mag))
        elif name == "feet_offset":
            for r in ("left_foot", "right_foot"):
                if r in tr:
                    tr[r] = shift(tr[r], (0, mag, 0))
        elif name == "waist_offset":
            if "waist" in tr:
                tr["waist"] = shift(tr["waist"], (mag, 0, 0))
        elif name == "noise":
            for r in tr:
                tr[r] = shift(tr[r], rng.normal(scale=mag, size=3))
        elif name == "rot_noise":
            for r in tr:
                axis = rng.normal(size=3)
                q = quat_from_axis_angle(axis, math.radians(rng.normal(scale=mag)))
                tr[r] = Transform(tr[r].position, quat_mul(q, tr[r].rotation))
        return tr

    return hook


# -- ladders ---------------------------------------------------------------------------

@dataclass
class LadderStep:
    spec: str
    score_deg: float
    end_effector_m: float


def is_monotonic(steps: Sequence[LadderStep], tolerance_deg: float = 0.05) -> bool:
    """True if every step scores worse (higher) than the previous one, within tolerance."""
    for a, b in zip(steps, steps[1:]):
        if b.score_deg < a.score_deg - tolerance_deg:
            return False
    return True


DEFAULT_TRACKER_LADDERS: Dict[str, List[str]] = {
    "hands_offset": ["hands_offset:0.05", "hands_offset:0.10", "hands_offset:0.20"],
    "head_yaw": ["head_yaw:10", "head_yaw:30", "head_yaw:60"],
    "feet_offset": ["feet_offset:0.05", "feet_offset:0.15", "feet_offset:0.30"],
    "noise": ["noise:0.01", "noise:0.03", "noise:0.08"],
}

DEFAULT_FRAME_LADDERS: Dict[str, List[str]] = {
    "rotate_all": ["rotate_all:2", "rotate_all:10", "rotate_all:30"],
    "rotate_arms": ["rotate_arms:5", "rotate_arms:20", "rotate_arms:45"],
    "jitter": ["jitter:3", "jitter:10", "jitter:25"],
}
