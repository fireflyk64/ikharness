"""Evaluate an IK implementation in the Godot harness against a reference dataset.

    python -m ikharness.run_godot --dataset out/datasets/mocap08.json --tracker-set 6pt \
        [--ik renik] [--settle 8] [--out-dir out/results]

Steps: build the tracker test file, run ``godot/harness/harness.gd`` headless,
load the result and score it. Prints the summary and writes
``<out-dir>/<dataset>_<ik>_<set>.{trackers,result,score}.json``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .dataset import Dataset
from .negative import make_tracker_perturbation
from .proc import run_guarded
from .scoring import score
from .testfile import HarnessResult, build_test_file

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "godot" / "harness"


def run_harness(trackers_path: Path, result_path: Path, ik: str, settle: int, timeout: float = 1800.0,
                shadermotion_dir: Optional[Path] = None) -> str:
    cmd = [os.environ.get("GODOT", "godot"), "--headless", "--path", str(HARNESS), "-s", "harness.gd", "--",
           "--trackers", str(trackers_path.resolve()), "--out", str(result_path.resolve()), "--ik", ik, "--settle", str(settle)]
    if shadermotion_dir is not None:
        cmd += ["--shadermotion-dir", str(shadermotion_dir.resolve())]
    if result_path.exists():
        result_path.unlink()
    res = run_guarded(cmd, timeout=timeout)
    log = res.log
    if res.killed or res.returncode != 0 or not result_path.exists():
        raise RuntimeError(f"harness failed (rc={res.returncode}, killed={res.killed or 'no'}, peak {res.peak_rss_mb:.0f} MB):\n{log[-4000:]}")
    return log


def evaluate(dataset_path: Path, tracker_set: str, ik: str = "renik", settle: int = 8,
             out_dir: Path = ROOT / "out" / "results", perturb: Optional[str] = None, readout: str = "json"):
    """Run one evaluation. ``perturb`` is ``name:magnitude[:seed]`` applied to the tracker inputs.

    ``readout="shadermotion"`` makes the harness also write every solved pose as a
    ShaderMotion PNG, reads the poses back from those pixels and scores them against the
    reference passed through the same format (so the format's floor cancels).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = Dataset.load(dataset_path)
    tag = f"_{perturb.replace(':', '-')}" if perturb else ""
    stem = f"{dataset_path.stem}_{ik}_{tracker_set}{tag}"
    trackers_path = out_dir / f"{stem}.trackers.json"
    result_path = out_dir / f"{stem}.result.json"
    score_path = out_dir / f"{stem}.score.json"
    hook = make_tracker_perturbation(perturb) if perturb else None
    build_test_file(dataset, tracker_set, trackers_path, dataset_path=str(dataset_path), perturb=hook,
                    perturb_name=perturb or "")
    if ik == "echo":
        # Scorer sanity check: the "solver" returns the reference poses.
        frames = list(dataset.frames)
        report = score(dataset, frames, implementation="echo", tracker_set=tracker_set)
        report.save(score_path)
        return report, "echo: no harness run"
    sm_dir = None
    if readout == "shadermotion":
        sm_dir = out_dir / f"{stem}.shadermotion"
        sm_dir.mkdir(parents=True, exist_ok=True)
        for old in sm_dir.glob("*.png"):
            old.unlink()
    elif readout != "json":
        raise ValueError(f"unknown readout {readout!r} (json, shadermotion)")
    log = run_harness(trackers_path, result_path, ik, settle, shadermotion_dir=sm_dir)
    result = HarnessResult.load(result_path)
    report = score(dataset, result.frames, implementation=result.implementation, tracker_set=tracker_set,
                   result_rest=result.rest)
    if sm_dir is not None:
        import json as _json

        from .shadermotion.readout import decode_directory, roundtrip_dataset
        decoded = decode_directory(sm_dir, dataset.skeleton, count=len(dataset.frames))
        via_pixels = score(roundtrip_dataset(dataset), decoded, implementation=f"{result.implementation}+shadermotion",
                           tracker_set=tracker_set)
        raw = score(dataset, decoded, implementation=f"{result.implementation}+shadermotion(raw ref)", tracker_set=tracker_set)
        doc = via_pixels.to_dict()
        doc["readout"] = "shadermotion"
        doc["json_readout_weighted_deg"] = report.weighted_score_deg
        doc["raw_reference_weighted_deg"] = raw.weighted_score_deg
        score_path.write_text(_json.dumps(doc, indent=1))
        via_pixels.json_readout = report
        via_pixels.raw_reference = raw
        return via_pixels, log
    report.save(score_path)
    return report, log


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--tracker-set", default="6pt", help="name from trackers.TRACKER_SETS or a comma separated role list")
    p.add_argument("--ik", default="renik")
    p.add_argument("--settle", type=int, default=8)
    p.add_argument("--out-dir", default=str(ROOT / "out" / "results"))
    p.add_argument("--perturb", default=None, help="name:magnitude[:seed], see ikharness.negative")
    p.add_argument("--readout", default="json", choices=["json", "shadermotion"],
                   help="read solved poses from the result JSON or back from ShaderMotion pixels")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    report, log = evaluate(Path(args.dataset), args.tracker_set, args.ik, args.settle, Path(args.out_dir), args.perturb,
                           args.readout)
    if args.verbose:
        print(log)
    print(report.summary())
    if args.readout == "shadermotion":
        print(f"readout comparison (weighted deg): through pixels {report.weighted_score_deg:.2f} | "
              f"direct JSON {report.json_readout.weighted_score_deg:.2f} | pixels vs raw reference {report.raw_reference.weighted_score_deg:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
