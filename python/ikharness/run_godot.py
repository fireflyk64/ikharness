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
from .scoring import score
from .testfile import HarnessResult, build_test_file

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "godot" / "harness"


def run_harness(trackers_path: Path, result_path: Path, ik: str, settle: int, timeout: float = 1800.0) -> str:
    cmd = [os.environ.get("GODOT", "godot"), "--headless", "--path", str(HARNESS), "-s", "harness.gd", "--",
           "--trackers", str(trackers_path.resolve()), "--out", str(result_path.resolve()), "--ik", ik, "--settle", str(settle)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    log = res.stdout + res.stderr
    if res.returncode != 0 or not result_path.exists():
        raise RuntimeError(f"harness failed (rc={res.returncode}):\n{log[-4000:]}")
    return log


def evaluate(dataset_path: Path, tracker_set: str, ik: str = "renik", settle: int = 8,
             out_dir: Path = ROOT / "out" / "results", perturb: Optional[str] = None):
    """Run one evaluation. ``perturb`` is ``name:magnitude[:seed]`` applied to the tracker inputs."""
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
    log = run_harness(trackers_path, result_path, ik, settle)
    result = HarnessResult.load(result_path)
    report = score(dataset, result.frames, implementation=result.implementation, tracker_set=tracker_set,
                   result_rest=result.rest)
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
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)
    report, log = evaluate(Path(args.dataset), args.tracker_set, args.ik, args.settle, Path(args.out_dir), args.perturb)
    if args.verbose:
        print(log)
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
