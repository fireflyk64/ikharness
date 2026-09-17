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
from typing import Optional

from .dataset import Dataset
from .proc import run_guarded

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "godot" / "tools"


def godot_binary() -> str:
    return os.environ.get("GODOT", "godot")


def export_clip(model: str, anim: str, out: str, frames: int, hips_mode: str, start: float, end: float,
                keep_root: bool = False, project: Optional[Path] = None) -> None:
    """Run the exporter. With ``project`` set, model/anim are ``res://`` paths inside that project."""
    def resolve(path: str) -> str:
        if path.startswith("res://"):
            return path
        if ":" in path and not Path(path).exists():
            base, clip = path.rsplit(":", 1)
            return f"{Path(base).resolve()}:{clip}"
        return str(Path(path).resolve())

    project_path = str(project) if project else str(TOOLS)
    script = "export_poses.gd" if not project else str(TOOLS / "export_poses.gd")
    cmd = [godot_binary(), "--headless", "--path", project_path, "-s", script, "--",
           "--model", resolve(model), "--anim", resolve(anim),
           "--out", str(Path(out).resolve()), "--frames", str(frames), "--hips-mode", hips_mode,
           "--start", str(start), "--end", str(end)]
    if keep_root:
        cmd.append("--keep-root")
    res = run_guarded(cmd, timeout=900)
    log = res.log
    if res.killed or res.returncode != 0 or "export_poses: wrote" not in log:
        raise RuntimeError(f"exporter failed for {anim} (killed={res.killed or 'no'}, peak {res.peak_rss_mb:.0f} MB):\n{log[-3000:]}")
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
    p.add_argument("--bone-map", default=None,
                   help="retarget the model through Godot's importer: preset (vrm, bvh_perfume, mixamo) or a JSON file")
    p.add_argument("--no-fix-silhouette", action="store_true", help="retarget: keep the source rest instead of forcing a T-pose")
    p.add_argument("--keep-project", default=None, help="retarget: keep the temporary import project at this path")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    merged = None
    with tempfile.TemporaryDirectory() as tmp:
        project = None
        model = args.model
        anims = list(args.anim)
        if args.bone_map:
            from .retarget import import_retargeted, load_bone_map
            bone_map = load_bone_map(args.bone_map)
            project = import_retargeted(Path(args.model), bone_map, Path(args.keep_project) if args.keep_project else Path(tmp) / "project",
                                        fix_silhouette=not args.no_fix_silhouette)
            model = f"res://{Path(args.model).name}"
            # Clips embedded in the model refer to it by name; other files are not retargeted.
            anims = [f"res://{Path(a.split(':')[0]).name}" + (":" + a.rsplit(":", 1)[1] if ":" in a and not Path(a).exists() else "")
                     if Path(a.split(":")[0]).resolve() == Path(args.model).resolve() else a for a in anims]
            print(f"retargeted {Path(args.model).name} with bone map {args.bone_map} (project {project})", file=sys.stderr)
        for i, anim in enumerate(anims):
            out = Path(tmp) / f"clip{i}.json"
            export_clip(model, anim, str(out), args.frames, args.hips_mode, args.start, args.end, args.keep_root, project)
            ds = Dataset.load(out)
            merged = ds if merged is None else merged.merge(ds)
    merged.save(args.out)
    print(f"wrote {args.out}: {len(merged.frames)} frames from {len(merged.sources)} clips, "
          f"{len(merged.skeleton.order)} bones, hips height {merged.skeleton.hips_height:.3f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
