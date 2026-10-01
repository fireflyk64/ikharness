"""Godot as preprocessor: write a dataset as GLB / Godot scene / Godot animation.

    ikh dataset export-retargeted --dataset D.json --out clip.glb [--fps 2]
        [--scene clip.tscn] [--animation clip.res] [--no-mesh] [--verify]

The files carry the standard humanoid skeleton (SkeletonProfileHumanoid names, T-pose
rest, the dataset's proportions) and one clip ``ikharness`` whose key ``i`` (at ``i / fps``
seconds) is dataset frame ``i``. Whatever rig the animation came from was retargeted when
the dataset was built, so this is the form to hand to Unity (glTFast / UnityGLTF, then a
Humanoid avatar: the bone names are Unity's), to an OpenXR application's own replay, or to
another Godot project.

``--verify`` imports the GLB again through the dataset exporter, samples it at the key
times and scores the result against the dataset: 0 degrees means the file holds exactly the
reference poses.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .dataset import Dataset
from .proc import run_guarded

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "godot" / "tools"
CLIP_NAME = "ikharness"


def export(dataset_path, out: Optional[Path] = None, fps: float = 2.0, scene: Optional[Path] = None,
           animation: Optional[Path] = None, mesh: bool = True) -> str:
    cmd = [os.environ.get("GODOT", "godot"), "--headless", "--path", str(TOOLS), "-s", "export_retargeted.gd", "--",
           "--dataset", str(Path(dataset_path).resolve()), "--fps", repr(float(fps))]
    for flag, path in (("--out", out), ("--scene", scene), ("--animation", animation)):
        if path:
            Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
            cmd += [flag, str(Path(path).resolve())]
    if not mesh:
        cmd.append("--no-mesh")
    res = run_guarded(cmd, timeout=600)
    if res.killed or res.returncode != 0 or "export_retargeted: wrote" not in res.log:
        raise RuntimeError(f"export failed (rc={res.returncode}, killed={res.killed or 'no'}):\n{res.log[-3000:]}")
    return res.log


def verify(dataset_path, glb: Path):
    """Re-import ``glb``, sample it at the key times and score it against the dataset."""
    from .build_dataset import export_clip
    from .scoring import score

    dataset = Dataset.load(dataset_path)
    with tempfile.TemporaryDirectory(prefix="ikh-export-") as tmp:
        back = Path(tmp) / "back.json"
        export_clip(str(glb), f"{glb}:{CLIP_NAME}", str(back), len(dataset.frames), "absolute", 0.0, 1.0)
        again = Dataset.load(back)
    report = score(dataset, again.frames, implementation="export round trip", result_rest={
        name: bone.rest_global for name, bone in again.skeleton.bones.items()})
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ikh dataset export-retargeted", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", default=None, help="binary glTF (.glb) with skeleton, body mesh and the clip")
    p.add_argument("--scene", default=None, help="Godot scene (.tscn / .scn)")
    p.add_argument("--animation", default=None, help="Godot Animation resource (.res / .tres)")
    p.add_argument("--fps", type=float, default=2.0, help="keys per second; dataset frame i sits at i / fps")
    p.add_argument("--no-mesh", action="store_true", help="Godot scene without the body shapes (the GLB always has them: its skeleton is a skin)")
    p.add_argument("--verify", action="store_true", help="re-import the GLB and score it against the dataset")
    args = p.parse_args(argv)
    if not (args.out or args.scene or args.animation):
        p.error("give --out, --scene or --animation")
    log = export(args.dataset, args.out, args.fps, args.scene, args.animation, not args.no_mesh)
    for line in log.splitlines():
        if line.startswith("export_retargeted:"):
            print(line)
    if args.verify:
        if not args.out:
            p.error("--verify needs --out (the GLB)")
        report = verify(args.dataset, Path(args.out))
        worst = max(report.bones.values(), key=lambda b: b.angle_max_deg)
        print(f"verify: re-imported {report.frames_scored} frames, body score {report.body_score_deg:.4f} deg, "
              f"worst bone {worst.bone} {worst.angle_max_deg:.4f} deg, end effectors {report.end_effector_position_mean_m * 1000:.2f} mm")
        if report.body_score_deg > 0.05 or report.frames_missing:
            print("verify: FAILED (the file does not hold the reference poses)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
