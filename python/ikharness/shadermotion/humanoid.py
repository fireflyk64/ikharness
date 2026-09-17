# SPDX-License-Identifier: Apache-2.0
#
# The swing-twist math and the conversion between bone rotations and Mecanim style
# swing-twist triplets are ported from godot-humanoid / V-Sekai godot-shader-motion
# (humanoid/transform_util.gd, human_trait.gd):
#   Copyright 2022-2023 lox9973, Copyright 2023-present Lyuma and contributors, Apache-2.0.
# The tables in human_trait.json are dumped from that project's human_trait.gd.
"""ShaderMotion pose layer: humanoid bone rotations <-> swing-twist slot values.

ShaderMotion stores, for every humanoid bone, swing-twist angles (twist about X, swing
about YZ) in Unity's calibrated muscle axes, relative to the Mecanim neutral pose:

    local_rotation = preQ * swing_twist(sign * angles) * postQ^-1

``preQ`` / ``postQ`` here are expressed for Godot's SkeletonProfileHumanoid rig, so the
local rotations are Godot bone-local *pose* rotations (absolute, not deltas from rest).
Not every rotation is representable (a hinge has no twist, fingers have two axes);
encoding projects onto the representable set and reports the residual, and passes the
residual on to the children the way Mecanim distributes twist.

The hips are different: world position in meters and the rotation matrix's y and z
columns, in Unity space. Unity is left handed with +X to the character's right; the Godot
profile faces +Z with +X to the character's left, so the conversion mirrors X.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..dataset import Frame, Skeleton
from ..mathutil import Transform, quat, quat_from_matrix, quat_inv, quat_mul, quat_to_matrix
from . import codec

_TRAIT_PATH = Path(__file__).with_name("human_trait.json")


@lru_cache(maxsize=1)
def trait() -> dict:
    d = json.loads(_TRAIT_PATH.read_text())
    d["index"] = {name: i for i, name in enumerate(d["godot_names"])}
    return d


def bone_index(godot_name: str) -> int:
    return trait()["index"][godot_name]


# -- swing twist --------------------------------------------------------------------------

def swing_twist(v) -> np.ndarray:
    """Radians (twist x, swing y, swing z) -> quaternion (x, y, z, w)."""
    x, y, z = (float(c) for c in v)
    yz = math.sqrt(y * y + z * z)
    sinc = 0.5 if abs(yz) < 1e-8 else math.sin(yz / 2) / yz
    swing_w = math.cos(yz / 2)
    twist_w = math.cos(x / 2)
    twist_x = math.sin(x / 2)
    return np.array([swing_w * twist_x, (z * twist_x + y * twist_w) * sinc, (z * twist_w - y * twist_x) * sinc, swing_w * twist_w])


def swing_twist_inv(q) -> np.ndarray:
    """Quaternion -> radians (twist x, swing y, swing z); inverse of :func:`swing_twist`."""
    a, b, c, d = (float(v) for v in q)
    if d < 0:  # same rotation, keeps the twist in (-pi, pi]
        a, b, c, d = -a, -b, -c, -d
    n = a * a + d * d
    twist_x = math.sqrt(a * a / n) if n > 1e-16 else 0.0
    twist_x = min(twist_x, 1.0)
    if a < 0:
        twist_x = -twist_x
    twist_w = math.sqrt(max(0.0, 1.0 - twist_x * twist_x))
    swing_w = d / twist_w if abs(twist_x) < 1e-8 else a / twist_x
    swing_w = max(-1.0, min(1.0, swing_w))
    x = math.asin(twist_x) * 2
    yz = math.acos(swing_w) * 2
    sinc = 0.5 if abs(yz) < 1e-8 else math.sin(yz / 2) / yz
    # b = (z*tx + y*tw)*sinc ; c = (z*tw - y*tx)*sinc  ->  solve the 2x2 system (tx^2 + tw^2 = 1)
    y = (b * twist_w - c * twist_x) / sinc
    z = (b * twist_x + c * twist_w) / sinc
    return np.array([x, y, z])


# -- single bones ---------------------------------------------------------------------------

def decode_bone(index: int, slots3) -> np.ndarray:
    """Slot triplet (angles/180deg, in [-1, 1]) -> Godot bone-local pose rotation."""
    t = trait()
    mfb = t["muscle_from_bone"][index]
    v = np.array([float(slots3[i]) * math.pi if mfb[i] != -1 else float(slots3[i]) for i in range(3)])
    v = v * np.array([1.0, -1.0, -1.0]) * np.array(t["signs"][index])
    return quat(quat_mul(quat_mul(quat(t["pre_q"][index]), swing_twist(v)), quat(t["post_q_inverse"][index])))


def encode_bone(index: int, rotation, leftover=None) -> Tuple[np.ndarray, float, np.ndarray]:
    """Godot bone-local pose rotation -> (slot triplet, projection residual in degrees, leftover).

    ``leftover`` is the parent's residual rotation; the returned leftover goes to this
    bone's children.
    """
    t = trait()
    mfb = t["muscle_from_bone"][index]
    rot = quat(rotation) if leftover is None else quat(quat_mul(leftover, quat(rotation)))
    pre = quat(t["pre_q"][index])
    inv_post = quat(t["post_q_inverse"][index])
    swing = quat(quat_mul(quat_mul(quat_inv(pre), rot), quat_inv(inv_post)))
    st = swing_twist_inv(swing)
    for i in range(3):
        if mfb[i] == -1:
            st[i] = 0.0
    slots = st * np.array([1.0, -1.0, -1.0]) * np.array(t["signs"][index])
    for i in range(3):
        if mfb[i] == -1:
            slots[i] = 0.0
        else:
            slots[i] = (slots[i] / math.pi + 1.0) % 2.0 - 1.0
    decoded = decode_bone(index, slots)
    residual = quat(quat_mul(quat_inv(decoded), rot))
    deg = math.degrees(2.0 * math.acos(min(1.0, abs(float(residual[3])))))
    return slots, deg, residual


# -- handedness ----------------------------------------------------------------------------------

_MIRROR_X = np.diag([-1.0, 1.0, 1.0])


def godot_to_unity_position(p) -> np.ndarray:
    return np.array([-p[0], p[1], p[2]], dtype=float)


unity_to_godot_position = godot_to_unity_position


def godot_to_unity_matrix(m: np.ndarray) -> np.ndarray:
    return _MIRROR_X @ m @ _MIRROR_X


unity_to_godot_matrix = godot_to_unity_matrix


# -- whole frames -------------------------------------------------------------------------------------

def frame_to_slots(frame: Frame, skeleton: Skeleton) -> Tuple[Dict[int, float], Dict[str, float]]:
    """Dataset frame (global bone poses) -> ({slot: value}, {bone: projection residual deg})."""
    slots: Dict[int, float] = {}
    residuals: Dict[str, float] = {}
    leftovers: Dict[str, np.ndarray] = {}
    names = trait()["index"]

    hips = frame.bones["Hips"]
    m = godot_to_unity_matrix(quat_to_matrix(hips.rotation))
    slots.update(codec.encode_hips(godot_to_unity_position(hips.position), m[:, 1], m[:, 2], skeleton.hips_height))

    for bone in skeleton.order:
        if bone == "Hips" or bone not in names or bone not in codec.BONE_SLOTS or bone not in frame.bones:
            continue
        parent = skeleton.bones[bone].parent
        if parent not in frame.bones:
            continue
        local = quat_mul(quat_inv(frame.bones[parent].rotation), frame.bones[bone].rotation)
        triplet, deg, leftover = encode_bone(names[bone], local, leftovers.get(parent))
        leftovers[bone] = leftover
        residuals[bone] = deg
        base, channels = codec.BONE_SLOTS[bone]
        for i, ch in enumerate(channels):
            slots[base + i] = float(triplet[ch])
    return slots, residuals


def slots_to_frame(slot_values: np.ndarray, skeleton: Skeleton, time: float = 0.0) -> Frame:
    """Decoded slot array -> dataset frame on ``skeleton`` (forward kinematics with its rest offsets)."""
    names = trait()["index"]
    hips = codec.decode_hips(slot_values)
    y, z = hips["rot_y"], hips["rot_z"]
    x = np.cross(y, z)
    m_unity = np.stack([x, y, z], axis=1)
    bones: Dict[str, Transform] = {}
    bones["Hips"] = Transform(unity_to_godot_position(hips["position"]), quat_from_matrix(unity_to_godot_matrix(m_unity)))
    for bone in skeleton.order:
        if bone == "Hips":
            continue
        parent = skeleton.bones[bone].parent
        if parent not in bones:
            continue
        rest_local = skeleton.bones[bone].rest_local
        if bone in names and bone in codec.BONE_SLOTS:
            base, channels = codec.BONE_SLOTS[bone]
            triplet = [0.0, 0.0, 0.0]
            for i, ch in enumerate(channels):
                triplet[ch] = float(slot_values[base + i])
            local_rot = decode_bone(names[bone], triplet)
        else:
            local_rot = rest_local.rotation
        bones[bone] = bones[parent] * Transform(rest_local.position, local_rot)
    return Frame(0, time, bones)
