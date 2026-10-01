"""T-pose calibration: reference implementation and the remote gesture.

The user (here: the reference rig) stands in the avatar's T-pose, holds still, and pulls
both triggers. From that one frame of tracker poses the application derives

* the avatar **root** in tracking space: yaw from the right-to-left hand line (no
  dependence on any device's local axes), position from the headset placed over the
  avatar's eye point, feet on the floor;
* one rigid **offset** per tracker relative to the bone it follows:
  ``tracker = root * bone_rest * offset``.

Afterwards ``bone_target = tracker * offset⁻¹`` for every frame. Whatever the trackers'
mounting (strap position, rotation) it is absorbed, as long as the T-pose was struck
exactly; the avatar's rest pose *is* the definition of that T-pose.

``godot/harness/calibration.gd`` implements the same math for the harness and the demo.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence

import numpy as np

from .dataset import Frame, Skeleton
from .mathutil import Transform, quat_from_axis_angle, quat_rotate
from .trackers import DEFAULT_RULES, TrackerRule, place_trackers, resolve_bone, rules_for

#: role -> candidate bones, first present wins (what an application knows without our rules).
ROLE_BONES: Dict[str, Sequence[str]] = {
    "head": ("Head",), "left_hand": ("LeftHand",), "right_hand": ("RightHand",), "waist": ("Hips",),
    "chest": ("UpperChest", "Chest"), "left_foot": ("LeftFoot",), "right_foot": ("RightFoot",),
    "left_knee": ("LeftLowerLeg",), "right_knee": ("RightLowerLeg",),
    "left_elbow": ("LeftLowerArm",), "right_elbow": ("RightLowerArm",),
    "left_shoulder": ("LeftUpperArm",), "right_shoulder": ("RightUpperArm",),
}


def role_bone(skeleton: Skeleton, role: str) -> Optional[str]:
    for bone in ROLE_BONES.get(role, ()):
        if skeleton.has(bone):
            return bone
    return None


def rest_frame(skeleton: Skeleton) -> Frame:
    return Frame(0, 0.0, {n: b.rest_global for n, b in skeleton.bones.items()})


def tpose_trackers(skeleton: Skeleton, roles: Sequence[str], rules: Optional[Mapping[str, TrackerRule]] = None) -> Dict[str, Transform]:
    """Tracker transforms (dataset space) while the reference rig stands in its T-pose."""
    return place_trackers(rest_frame(skeleton), skeleton, list(roles), rules or rules_for(skeleton))


def eye_point(skeleton: Skeleton) -> np.ndarray:
    """The avatar's view point in its rest pose: eye midpoint, or the head joint without eye bones."""
    if skeleton.has("LeftEye") and skeleton.has("RightEye"):
        return 0.5 * (skeleton.bones["LeftEye"].rest_global.position + skeleton.bones["RightEye"].rest_global.position)
    return skeleton.bones["Head"].rest_global.position.copy()


@dataclass
class Calibration:
    root: Transform  # avatar root in tracking space
    offsets: Dict[str, Transform]  # role -> tracker relative to its bone
    bones: Dict[str, str]  # role -> bone name
    height_ratio: float  # headset height over avatar eye height (1.0 when the user fits the avatar)

    def bone_target(self, role: str, tracker: Transform) -> Transform:
        """Bone pose in skeleton (avatar root) space for a tracker pose in tracking space."""
        return self.root.inverse() * tracker * self.offsets[role].inverse()


def solve_root(skeleton: Skeleton, tpose: Mapping[str, Transform]) -> Transform:
    """Yaw and floor position of the avatar that overlays its rest pose on the T-pose trackers."""
    up = np.array([0.0, 1.0, 0.0])
    left = None
    for a, b in (("left_hand", "right_hand"), ("left_foot", "right_foot"), ("left_elbow", "right_elbow")):
        if a in tpose and b in tpose:
            v = tpose[a].position - tpose[b].position
            v[1] = 0.0
            if np.linalg.norm(v) > 1e-4:
                left = v / np.linalg.norm(v)
                break
    if left is None:
        # No left/right pair: fall back to the headset's view direction (-Z in OpenXR) as forward.
        fwd = quat_rotate(tpose["head"].rotation, np.array([0.0, 0.0, -1.0]))
        fwd[1] = 0.0
        fwd = fwd / np.linalg.norm(fwd)
    else:
        fwd = np.cross(left, up)  # avatar +X is its left, +Z its front
    yaw = math.atan2(fwd[0], fwd[2])
    rot = quat_from_axis_angle((0, 1, 0), yaw)
    pos = np.zeros(3)
    if "head" in tpose:
        eye = eye_point(skeleton)
        offset = quat_rotate(rot, np.array([eye[0], 0.0, eye[2]]))
        pos = np.array([tpose["head"].position[0] - offset[0], 0.0, tpose["head"].position[2] - offset[2]])
    return Transform(pos, rot)


def calibrate(skeleton: Skeleton, tpose: Mapping[str, Transform]) -> Calibration:
    root = solve_root(skeleton, tpose)
    offsets: Dict[str, Transform] = {}
    bones: Dict[str, str] = {}
    for role, tracker in tpose.items():
        bone = role_bone(skeleton, role)
        if bone is None:
            continue
        bones[role] = bone
        offsets[role] = (root * skeleton.bones[bone].rest_global).inverse() * tracker
    ratio = 1.0
    if "head" in tpose:
        eye_y = float(eye_point(skeleton)[1])
        ratio = float(tpose["head"].position[1]) / eye_y if eye_y > 1e-6 else 1.0
    return Calibration(root, offsets, bones, ratio)


# -- the gesture, through the Monado driver ---------------------------------------------------

def run_calibration_gesture(client, poses: Mapping[str, "object"], hold: float = 1.0, press: float = 0.3,
                            frame_id: int = 0, log=print) -> None:
    """Send the T-pose, hold still, pull both triggers, release.

    ``poses`` maps driver device names to :class:`ikharness.protocol.Pose` (already in the
    runtime's stage space). The application under test is expected to calibrate on the
    rising edge of both triggers.
    """
    from .protocol import Inputs

    client.send_inputs({"left_hand": Inputs(), "right_hand": Inputs()})
    client.send_frame(dict(poses), frame_id=frame_id)
    log(f"calibration: T-pose sent, holding {hold:.1f}s")
    time.sleep(hold)
    client.send_inputs({"left_hand": Inputs(trigger=1.0), "right_hand": Inputs(trigger=1.0)})
    log(f"calibration: both triggers pulled for {press:.1f}s")
    time.sleep(press)
    client.send_inputs({"left_hand": Inputs(), "right_hand": Inputs()})
    log("calibration: triggers released")
