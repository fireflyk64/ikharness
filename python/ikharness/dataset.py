"""Reference pose datasets (``ikharness-poses/1``).

A dataset holds one humanoid skeleton (the T-pose rest, in Godot's
SkeletonProfileHumanoid convention: +Y up, character faces +Z, bone local +Y
along the bone) and a list of sampled frames, each giving the global,
skeleton-space transform of every humanoid bone. Datasets are produced by
``godot/tools/export_poses.gd`` and consumed by the tracker placement,
harness drivers and the scorer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np

from .mathutil import Transform, quat_inv, quat_mul

FORMAT = "ikharness-poses/1"

# Bones that IK systems are expected to solve for; fingers, eyes and jaw are excluded from scoring.
BODY_BONES = [
    "Hips", "Spine", "Chest", "UpperChest", "Neck", "Head",
    "LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
    "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand",
    "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes",
    "RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes",
]


@dataclass
class Bone:
    name: str
    parent: str
    rest_local: Transform
    rest_global: Transform


@dataclass
class Skeleton:
    bones: Dict[str, Bone]
    order: List[str]
    hips_height: float
    head_height: float
    eye_height: float
    source_model: str = ""
    convention: str = "godot-humanoid"

    def has(self, name: str) -> bool:
        return name in self.bones

    def children(self, name: str) -> List[str]:
        return [b for b in self.order if self.bones[b].parent == name]

    def limb_length(self, name: str) -> float:
        """Distance from this bone's joint to its first child's joint (0 for leaves)."""
        kids = self.children(name)
        if not kids:
            return 0.0
        a = self.bones[name].rest_global.position
        b = self.bones[kids[0]].rest_global.position
        return float(np.linalg.norm(b - a))

    def limb_lengths(self) -> Dict[str, float]:
        return {b: self.limb_length(b) for b in self.order if b in BODY_BONES}

    def to_dict(self) -> dict:
        return {
            "convention": self.convention,
            "source_model": self.source_model,
            "hips_height": self.hips_height,
            "head_height": self.head_height,
            "eye_height": self.eye_height,
            "bones": [
                {
                    "name": b.name,
                    "parent": b.parent,
                    "rest_local": dict(zip(("position", "rotation"), b.rest_local.as_lists())),
                    "rest_global": dict(zip(("position", "rotation"), b.rest_global.as_lists())),
                }
                for b in (self.bones[n] for n in self.order)
            ],
        }

    @staticmethod
    def from_dict(d: dict) -> "Skeleton":
        bones: Dict[str, Bone] = {}
        order: List[str] = []
        for b in d["bones"]:
            bones[b["name"]] = Bone(
                name=b["name"],
                parent=b.get("parent", ""),
                rest_local=Transform(b["rest_local"]["position"], b["rest_local"]["rotation"]),
                rest_global=Transform(b["rest_global"]["position"], b["rest_global"]["rotation"]),
            )
            order.append(b["name"])
        return Skeleton(
            bones=bones,
            order=order,
            hips_height=float(d["hips_height"]),
            head_height=float(d["head_height"]),
            eye_height=float(d.get("eye_height", d["head_height"])),
            source_model=d.get("source_model", ""),
            convention=d.get("convention", "godot-humanoid"),
        )


@dataclass
class Frame:
    source: int
    time: float
    bones: Dict[str, Transform]  # global, skeleton space

    def delta_from_rest(self, skeleton: Skeleton, bone: str) -> np.ndarray:
        """Rotation that takes the bone's rest orientation to its posed orientation (world space)."""
        return quat_mul(self.bones[bone].rotation, quat_inv(skeleton.bones[bone].rest_global.rotation))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "time": self.time,
            "bones": {n: dict(zip(("position", "rotation"), t.as_lists())) for n, t in self.bones.items()},
        }

    @staticmethod
    def from_dict(d: dict) -> "Frame":
        return Frame(
            source=int(d.get("source", 0)),
            time=float(d.get("time", 0.0)),
            bones={n: Transform(t["position"], t["rotation"]) for n, t in d["bones"].items()},
        )


@dataclass
class Dataset:
    skeleton: Skeleton
    frames: List[Frame]
    sources: List[dict] = field(default_factory=list)
    generator: dict = field(default_factory=dict)

    @staticmethod
    def load(path) -> "Dataset":
        d = json.loads(Path(path).read_text())
        if d.get("format") != FORMAT:
            raise ValueError(f"{path}: unsupported format {d.get('format')!r}, want {FORMAT}")
        return Dataset(
            skeleton=Skeleton.from_dict(d["skeleton"]),
            frames=[Frame.from_dict(f) for f in d["frames"]],
            sources=list(d.get("sources", [])),
            generator=dict(d.get("generator", {})),
        )

    def save(self, path) -> None:
        doc = {
            "format": FORMAT,
            "generator": self.generator,
            "skeleton": self.skeleton.to_dict(),
            "sources": self.sources,
            "frames": [f.to_dict() for f in self.frames],
        }
        Path(path).write_text(json.dumps(doc))

    def body_bones(self) -> List[str]:
        return [b for b in BODY_BONES if self.skeleton.has(b)]

    def merge(self, other: "Dataset") -> "Dataset":
        """Append another dataset's frames (must share the skeleton); source ids are renumbered."""
        if other.skeleton.order != self.skeleton.order:
            raise ValueError("cannot merge datasets with different skeletons")
        offset = len(self.sources)
        sources = self.sources + [dict(s, id=s.get("id", i) + offset) for i, s in enumerate(other.sources)]
        frames = self.frames + [Frame(f.source + offset, f.time, f.bones) for f in other.frames]
        return Dataset(self.skeleton, frames, sources, self.generator)
