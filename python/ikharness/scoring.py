"""Scoring an IK result against the reference poses.

Both inputs are frames of global bone transforms. Per bone and frame the score
is the angular distance (degrees) between the *rest-relative* orientations
``pose × rest⁻¹`` of reference and result, each side using its own rig's rest
(T-pose) orientation, so rigs with different bone axes compare equal when they
strike the same pose. Positional error (meters) is reported as well, which
matters for end effectors and for hips placement.

Aggregates: mean, median, 95th percentile per bone, an overall body score
(mean over the scored bones of their mean angular error) and a weighted
variant that emphasises the large body segments.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .dataset import BODY_BONES, Dataset, Frame
from .mathutil import Transform, quat_angle, quat_inv, quat_mul

# Larger segments dominate how an avatar "looks"; fingers are not scored at all.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "Hips": 2.0, "Spine": 1.5, "Chest": 1.5, "UpperChest": 1.0, "Neck": 0.5, "Head": 1.5,
    "LeftShoulder": 0.5, "LeftUpperArm": 1.5, "LeftLowerArm": 1.5, "LeftHand": 1.0,
    "RightShoulder": 0.5, "RightUpperArm": 1.5, "RightLowerArm": 1.5, "RightHand": 1.0,
    "LeftUpperLeg": 1.5, "LeftLowerLeg": 1.5, "LeftFoot": 1.0, "LeftToes": 0.25,
    "RightUpperLeg": 1.5, "RightLowerLeg": 1.5, "RightFoot": 1.0, "RightToes": 0.25,
}


@dataclass
class BoneStats:
    bone: str
    count: int
    angle_mean_deg: float
    angle_median_deg: float
    angle_p95_deg: float
    angle_max_deg: float
    position_mean_m: float
    position_p95_m: float


@dataclass
class ScoreReport:
    implementation: str
    tracker_set: str
    frames_scored: int
    frames_missing: int
    bones: Dict[str, BoneStats]
    body_score_deg: float  # unweighted mean of per-bone mean angular error
    weighted_score_deg: float
    end_effector_position_mean_m: float
    per_frame_mean_deg: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "implementation": self.implementation,
            "tracker_set": self.tracker_set,
            "frames_scored": self.frames_scored,
            "frames_missing": self.frames_missing,
            "body_score_deg": self.body_score_deg,
            "weighted_score_deg": self.weighted_score_deg,
            "end_effector_position_mean_m": self.end_effector_position_mean_m,
            "bones": {b: vars(s) for b, s in self.bones.items()},
            "per_frame_mean_deg": self.per_frame_mean_deg,
        }

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1))

    def summary(self) -> str:
        lines = [
            f"{self.implementation} / {self.tracker_set}: body score {self.body_score_deg:.2f} deg "
            f"(weighted {self.weighted_score_deg:.2f}), end effectors {self.end_effector_position_mean_m * 100:.1f} cm, "
            f"{self.frames_scored} frames" + (f", {self.frames_missing} missing" if self.frames_missing else ""),
            f"{'bone':<16}{'mean':>8}{'median':>8}{'p95':>8}{'max':>8}{'pos cm':>8}",
        ]
        for b, s in self.bones.items():
            lines.append(
                f"{b:<16}{s.angle_mean_deg:8.2f}{s.angle_median_deg:8.2f}{s.angle_p95_deg:8.2f}{s.angle_max_deg:8.2f}{s.position_mean_m * 100:8.1f}"
            )
        return "\n".join(lines)


END_EFFECTORS = ["Head", "LeftHand", "RightHand", "LeftFoot", "RightFoot"]


def score(
    reference: Dataset,
    results: Sequence[Optional[Frame]],
    implementation: str = "unknown",
    tracker_set: str = "unknown",
    bones: Optional[Iterable[str]] = None,
    weights: Optional[Dict[str, float]] = None,
    result_rest: Optional[Dict[str, Transform]] = None,
) -> ScoreReport:
    """Score ``results[i]`` against ``reference.frames[i]``; ``None`` entries count as missing.

    ``result_rest`` is the rig-under-test's global T-pose per bone; when omitted the
    reference rest is assumed (same rig).
    """
    weights = weights or DEFAULT_WEIGHTS
    bone_list = [b for b in (bones or reference.body_bones()) if reference.skeleton.has(b)]
    ref_rest_inv = {b: quat_inv(reference.skeleton.bones[b].rest_global.rotation) for b in bone_list}
    res_rest_inv = {}
    for b in bone_list:
        if result_rest and b in result_rest:
            res_rest_inv[b] = quat_inv(result_rest[b].rotation)
        else:
            res_rest_inv[b] = ref_rest_inv[b]
    angles: Dict[str, List[float]] = {b: [] for b in bone_list}
    positions: Dict[str, List[float]] = {b: [] for b in bone_list}
    per_frame: List[float] = []
    missing = 0
    for ref, res in zip(reference.frames, results):
        if res is None:
            missing += 1
            continue
        frame_angles = []
        for b in bone_list:
            if b not in res.bones:
                continue
            d_ref = quat_mul(ref.bones[b].rotation, ref_rest_inv[b])
            d_res = quat_mul(res.bones[b].rotation, res_rest_inv[b])
            a = math.degrees(quat_angle(d_ref, d_res))
            p = float(np.linalg.norm(ref.bones[b].position - res.bones[b].position))
            angles[b].append(a)
            positions[b].append(p)
            frame_angles.append(a)
        per_frame.append(float(np.mean(frame_angles)) if frame_angles else float("nan"))

    stats: Dict[str, BoneStats] = {}
    for b in bone_list:
        if not angles[b]:
            continue
        a = np.array(angles[b])
        p = np.array(positions[b])
        stats[b] = BoneStats(
            bone=b,
            count=len(a),
            angle_mean_deg=float(a.mean()),
            angle_median_deg=float(np.median(a)),
            angle_p95_deg=float(np.percentile(a, 95)),
            angle_max_deg=float(a.max()),
            position_mean_m=float(p.mean()),
            position_p95_m=float(np.percentile(p, 95)),
        )
    means = [s.angle_mean_deg for s in stats.values()]
    body = float(np.mean(means)) if means else float("nan")
    w = np.array([weights.get(b, 1.0) for b in stats])
    weighted = float(np.sum(w * np.array(means)) / np.sum(w)) if means else float("nan")
    ee = [stats[b].position_mean_m for b in END_EFFECTORS if b in stats]
    return ScoreReport(
        implementation=implementation,
        tracker_set=tracker_set,
        frames_scored=len(reference.frames) - missing,
        frames_missing=missing,
        bones=stats,
        body_score_deg=body,
        weighted_score_deg=weighted,
        end_effector_position_mean_m=float(np.mean(ee)) if ee else float("nan"),
        per_frame_mean_deg=per_frame,
    )


def frames_from_result_file(path) -> List[Optional[Frame]]:
    """Load a harness result file: ``{"frames": [{"index": i, "bones": {...}} | null, ...]}``."""
    d = json.loads(Path(path).read_text())
    out: List[Optional[Frame]] = []
    for f in d["frames"]:
        out.append(None if f is None else Frame.from_dict({"source": 0, "time": f.get("time", 0.0), "bones": f["bones"]}))
    return out
