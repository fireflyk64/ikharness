"""Tracker test files (``ikharness-trackers/1``) and harness result files (``ikharness-result/1``).

A test file is what a harness consumes: the skeleton (so the harness can build
an identical rig), the tracker rules (bone + offset per role, so the harness
can turn a tracker pose back into a bone target, which is exactly what a
T-pose calibration with trackers placed on the bones would yield), and per
frame the tracker poses in dataset space.

A result file is what a harness produces: per frame the global pose of every
humanoid bone after IK, or ``null`` if the frame could not be solved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .dataset import Dataset, Frame
from .mathutil import Transform
from .trackers import TRACKER_SETS, TrackerRule, place_trackers, resolve_bone, rules_for

TRACKERS_FORMAT = "ikharness-trackers/1"
RESULT_FORMAT = "ikharness-result/1"


def build_test_file(dataset: Dataset, tracker_set: str, path, rules: Optional[Dict[str, TrackerRule]] = None,
                    dataset_path: str = "") -> dict:
    roles = TRACKER_SETS[tracker_set] if tracker_set in TRACKER_SETS else tracker_set.split(",")
    rules = rules or rules_for(dataset.skeleton)
    rules_out = {}
    for role in roles:
        r = rules[role]
        bone = resolve_bone(dataset.skeleton, r)
        if bone is None:
            raise KeyError(f"tracker {role!r}: bone {r.bone!r} not in skeleton")
        rules_out[role] = {"bone": bone, "offset": list(r.offset), "rotation": list(r.rotation)}
    frames = []
    for i, f in enumerate(dataset.frames):
        tr = place_trackers(f, dataset.skeleton, roles, rules)
        frames.append({
            "index": i,
            "time": f.time,
            "source": f.source,
            "trackers": {role: dict(zip(("position", "rotation"), t.as_lists())) for role, t in tr.items()},
        })
    doc = {
        "format": TRACKERS_FORMAT,
        "dataset": str(dataset_path),
        "tracker_set": tracker_set,
        "roles": roles,
        "skeleton": dataset.skeleton.to_dict(),
        "rules": rules_out,
        "frames": frames,
    }
    Path(path).write_text(json.dumps(doc))
    return doc


@dataclass
class HarnessResult:
    implementation: str
    tracker_set: str
    frames: List[Optional[Frame]]
    meta: dict

    @staticmethod
    def load(path) -> "HarnessResult":
        d = json.loads(Path(path).read_text())
        if d.get("format") != RESULT_FORMAT:
            raise ValueError(f"{path}: unsupported format {d.get('format')!r}")
        frames: List[Optional[Frame]] = []
        for f in d["frames"]:
            if f is None:
                frames.append(None)
            else:
                frames.append(Frame(source=0, time=float(f.get("time", 0.0)),
                                    bones={n: Transform(t["position"], t["rotation"]) for n, t in f["bones"].items()}))
        return HarnessResult(
            implementation=d.get("implementation", "unknown"),
            tracker_set=d.get("tracker_set", "unknown"),
            frames=frames,
            meta={k: v for k, v in d.items() if k not in ("frames",)},
        )
