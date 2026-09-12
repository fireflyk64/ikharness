"""Build a reference pose dataset from one model and one or more clips using the Godot exporter.

    python -m ikharness.build_dataset --model avatar.glb --anim clip.tres[:name] [--anim ...] \
        --frames 40 --hips-mode absolute|normalized|scale:f|ratio:h --out dataset.json

Every clip is exported separately by ``godot/tools/export_poses.gd`` and the
results are merged into one dataset (same skeleton, sources renumbered).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .dataset import Dataset

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "godot" / "tools"


def godot_binary() -> str:
    return os.environ.get("GODOT", "godot")


def export_clip(model: str, anim: str, out: str, frames: int, hips_mode: str, start: float, end: float,
                keep_root: bool = False) -> None:
    cmd = [godot_binary(), "--headless", "--path", str(TOOLS), "-s", "export_poses.gd", "--",
           "--model", str(Path(model).resolve()), "--anim", anim if ":" in anim and not Path(anim).exists() else str(Path(anim).resolve()),
           "--out", str(Path(out).resolve()), "--frames", str(frames), "--hips-mode", hips_mode,
           "--start", str(start), "--end", str(end)]
    if keep_root:
        cmd.append("--keep-root")
    res = subprocess.run(cmd, capture_output=True, text=True)
    log = (res.stdout + res.stderr)
    if res.returncode != 0 or "export_poses: wrote" not in log:
        raise RuntimeError(f"exporter failed for {anim}:\n{log[-3000:]}")
    for line in log.splitlines():
        if line.startswith("export_poses:"):
            print(line, file=sys.stderr)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", required=True)
    p.add_argument("--anim", action="append", required=True, help="clip path, optionally :name for glb clips; repeatable")
    p.add_argument("--frames", type=int, default=40, help="frames sampled per clip")
    p.add_argument("--hips-mode", default="absolute")
    p.add_argument("--start", type=float, default=0.02)
    p.add_argument("--end", type=float, default=0.98)
    p.add_argument("--keep-root", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    merged = None
    with tempfile.TemporaryDirectory() as tmp:
        for i, anim in enumerate(args.anim):
            out = Path(tmp) / f"clip{i}.json"
            export_clip(args.model, anim, str(out), args.frames, args.hips_mode, args.start, args.end, args.keep_root)
            ds = Dataset.load(out)
            merged = ds if merged is None else merged.merge(ds)
    merged.save(args.out)
    print(f"wrote {args.out}: {len(merged.frames)} frames from {len(merged.sources)} clips, "
          f"{len(merged.skeleton.order)} bones, hips height {merged.skeleton.hips_height:.3f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
