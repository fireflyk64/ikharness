"""Virtual tracker placement on a posed reference skeleton.

A tracker is rigidly attached to a bone: ``tracker = bone_global * offset``,
where ``offset`` is expressed in the bone's local (Godot humanoid) frame. The
same placement rule is applied to the reference pose and, implicitly, by the
IK system under test, so systematic offsets cancel out as long as the tracker
set is described in one place: here.

Tracker sets are named subsets of roles; roles use the SteamVR vocabulary
(``head``, ``left_hand``, ``right_hand``, ``waist``, ``chest``, ``left_foot``,
``right_foot``, ``left_knee``, ``right_knee``, ``left_elbow``, ``right_elbow``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional

import numpy as np

from .dataset import Frame, Skeleton
from .mathutil import Transform, quat_mul, quat_rotate, yaw180


@dataclass(frozen=True)
class TrackerRule:
    role: str
    bone: str
    #: offset in the bone's local frame (meters, x/y/z), applied after the bone rotation
    offset: tuple = (0.0, 0.0, 0.0)
    #: rotation offset in the bone frame (x, y, z, w)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    #: fall-back bone if ``bone`` is missing from the skeleton
    fallback_bone: Optional[str] = None


# Default placement. Head: at the eye midpoint, which is what an HMD reports.
# Hands: at the hand bone (wrist). Waist/chest/knees/elbows: on the joint.
DEFAULT_RULES: Dict[str, TrackerRule] = {
    "head": TrackerRule("head", "Head"),
    "left_hand": TrackerRule("left_hand", "LeftHand"),
    "right_hand": TrackerRule("right_hand", "RightHand"),
    "waist": TrackerRule("waist", "Hips"),
    "chest": TrackerRule("chest", "UpperChest", fallback_bone="Chest"),
    "left_foot": TrackerRule("left_foot", "LeftFoot"),
    "right_foot": TrackerRule("right_foot", "RightFoot"),
    "left_knee": TrackerRule("left_knee", "LeftLowerLeg"),
    "right_knee": TrackerRule("right_knee", "RightLowerLeg"),
    "left_elbow": TrackerRule("left_elbow", "LeftLowerArm"),
    "right_elbow": TrackerRule("right_elbow", "RightLowerArm"),
    "left_shoulder": TrackerRule("left_shoulder", "LeftUpperArm"),
    "right_shoulder": TrackerRule("right_shoulder", "RightUpperArm"),
}

TRACKER_SETS: Dict[str, List[str]] = {
    "3pt": ["head", "left_hand", "right_hand"],
    "4pt": ["head", "left_hand", "right_hand", "waist"],
    "6pt": ["head", "left_hand", "right_hand", "waist", "left_foot", "right_foot"],
    "7pt": ["head", "left_hand", "right_hand", "waist", "chest", "left_foot", "right_foot"],
    "8pt": ["head", "left_hand", "right_hand", "waist", "left_foot", "right_foot", "left_elbow", "right_elbow"],
    "10pt": ["head", "left_hand", "right_hand", "waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow"],
    "11pt": ["head", "left_hand", "right_hand", "waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow", "right_elbow"],
}


def head_rule_for(skeleton: Skeleton) -> TrackerRule:
    """Head tracker at the eye midpoint, derived from the skeleton's eye bones when present."""
    if skeleton.has("LeftEye") and skeleton.has("RightEye") and skeleton.has("Head"):
        head = skeleton.bones["Head"].rest_global
        eyes = 0.5 * (skeleton.bones["LeftEye"].rest_global.position + skeleton.bones["RightEye"].rest_global.position)
        local = head.inverse().apply(eyes)
        return TrackerRule("head", "Head", offset=tuple(float(v) for v in local))
    # No eye bones: a generic 8 cm up, 9 cm forward (+Z is forward in this convention).
    head = skeleton.bones["Head"].rest_global
    local = head.inverse().apply(head.position + np.array([0.0, 0.08, 0.09]))
    return TrackerRule("head", "Head", offset=tuple(float(v) for v in local))


def rules_for(skeleton: Skeleton, overrides: Optional[Mapping[str, TrackerRule]] = None) -> Dict[str, TrackerRule]:
    rules = dict(DEFAULT_RULES)
    rules["head"] = head_rule_for(skeleton)
    if overrides:
        rules.update(overrides)
    return rules


def resolve_bone(skeleton: Skeleton, rule: TrackerRule) -> Optional[str]:
    if skeleton.has(rule.bone):
        return rule.bone
    if rule.fallback_bone and skeleton.has(rule.fallback_bone):
        return rule.fallback_bone
    return None


def place_trackers(
    frame: Frame,
    skeleton: Skeleton,
    roles: List[str],
    rules: Optional[Mapping[str, TrackerRule]] = None,
) -> Dict[str, Transform]:
    """Tracker transforms in dataset (skeleton) space for one frame."""
    rules = rules or rules_for(skeleton)
    out: Dict[str, Transform] = {}
    for role in roles:
        rule = rules[role]
        bone = resolve_bone(skeleton, rule)
        if bone is None:
            raise KeyError(f"tracker {role!r}: bone {rule.bone!r} not in skeleton")
        out[role] = frame.bones[bone] * Transform(rule.offset, rule.rotation)
    return out


def to_openxr_stage(t: Transform) -> Transform:
    """Dataset space (faces +Z) to OpenXR stage space (faces -Z): yaw by 180 degrees about +Y."""
    q = yaw180()
    return Transform(quat_rotate(q, t.position), quat_mul(q, t.rotation))


def from_openxr_stage(t: Transform) -> Transform:
    return to_openxr_stage(t)  # the yaw is its own inverse
