"""Reading poses back from ShaderMotion images, and the matching reference round trip."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from ..dataset import Dataset, Frame, Skeleton
from . import codec, humanoid


def load_image(path) -> np.ndarray:
    from PIL import Image
    return np.array(Image.open(path).convert("RGB"))


def decode_image(image: np.ndarray, skeleton: Skeleton, time: float = 0.0, layer: int = 0, grid_w: int = codec.GRID_W) -> Frame:
    return humanoid.slots_to_frame(codec.decode_frame(image, layer=layer, grid_w=grid_w), skeleton, time)


def decode_directory(path, skeleton: Skeleton, count: Optional[int] = None) -> List[Optional[Frame]]:
    """``frame_<index>.png`` files -> frames aligned by index (missing indices are ``None``)."""
    files = {}
    for f in Path(path).glob("*.png"):
        m = re.search(r"(\d+)\.png$", f.name)
        if m:
            files[int(m.group(1))] = f
    n = count if count is not None else (max(files) + 1 if files else 0)
    return [decode_image(load_image(files[i]), skeleton) if i in files else None for i in range(n)]


def roundtrip_frames(frames: Sequence[Frame], skeleton: Skeleton, size=(640, 360), propagate_leftovers: bool = True) -> List[Frame]:
    """Reference poses as ShaderMotion can express them: frame -> slots -> 8 bit image -> frame.

    Use ``propagate_leftovers=False`` to mirror a shader encoder, which cannot hand one
    bone's unrepresentable twist on to its children.
    """
    out = []
    for f in frames:
        if f is None:
            out.append(None)
            continue
        slots, _ = humanoid.frame_to_slots(f, skeleton, propagate_leftovers)
        out.append(humanoid.slots_to_frame(codec.decode_frame(codec.encode_frame(slots, *size)), skeleton, f.time))
    return out


def roundtrip_dataset(dataset: Dataset, size=(640, 360), propagate_leftovers: bool = True) -> Dataset:
    return Dataset(dataset.skeleton, roundtrip_frames(dataset.frames, dataset.skeleton, size, propagate_leftovers),
                   dataset.sources, dataset.generator)
