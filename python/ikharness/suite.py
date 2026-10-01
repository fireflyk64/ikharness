"""Suites: many datasets × tracker sets → one number per implementation.

    ikh suite --ik renik [--suite suites/default.json] [--build]
              [--readout json|shadermotion|shadermotion-gpu|xr] [--calibration rules|tpose]

``--readout`` chooses how solved poses come back: the harness's result file, ShaderMotion
pixels (CPU encoder or the rendered recorder shader), or ``xr``: the whole OpenXR chain
(Monado, the Godot XR demo, T-pose calibration by triggers, pixels off the screen).

``final_deg`` is the weight-averaged ``weighted_score_deg`` over the entries (lower is
better); ``quality = 100 * exp(-final_deg / 25)`` is a 0..100 convenience mapping.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .run_godot import ROOT, evaluate

QUALITY_SCALE_DEG = 25.0


def quality_from_deg(deg: float) -> float:
    return 100.0 * math.exp(-deg / QUALITY_SCALE_DEG)


def expand(path: str) -> Path:
    """Recipe paths: ``${IKH_DATA_DIR}`` (default ~/dev/animations), ``~`` and repo-relative paths."""
    data_dir = os.environ.get("IKH_DATA_DIR", str(Path.home() / "dev" / "animations"))
    p = Path(os.path.expanduser(path.replace("${IKH_DATA_DIR}", data_dir)))
    return p if p.is_absolute() else ROOT / p


@dataclass
class SuiteEntryResult:
    dataset: str
    tracker_set: str
    weight: float
    body_score_deg: float
    weighted_score_deg: float
    end_effector_m: float
    frames: int
    json_readout_weighted_deg: Optional[float] = None  # the same run read from the result file, for pixel readouts


@dataclass
class SuiteReport:
    suite: str
    implementation: str
    entries: List[SuiteEntryResult]
    final_deg: float
    quality: float
    seconds: float
    readout: str = "json"
    calibration: str = "rules"

    def to_dict(self) -> dict:
        return {
            "suite": self.suite,
            "implementation": self.implementation,
            "readout": self.readout,
            "calibration": self.calibration,
            "final_deg": self.final_deg,
            "quality": self.quality,
            "seconds": self.seconds,
            "entries": [vars(e) for e in self.entries],
        }

    def summary(self) -> str:
        how = "" if (self.readout, self.calibration) == ("json", "rules") else f" [readout {self.readout}, calibration {self.calibration}]"
        lines = [f"suite {self.suite} / {self.implementation}{how}: FINAL {self.final_deg:.2f} deg  (quality {self.quality:.1f}/100, {self.seconds:.0f}s)",
                 f"{'dataset':<18}{'set':<7}{'weight':>7}{'body':>8}{'weighted':>10}{'ee cm':>7}{'frames':>8}"]
        for e in self.entries:
            lines.append(f"{e.dataset:<18}{e.tracker_set:<7}{e.weight:7.1f}{e.body_score_deg:8.2f}{e.weighted_score_deg:10.2f}{e.end_effector_m * 100:7.1f}{e.frames:8d}")
        return "\n".join(lines)


def load_suite(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def ensure_dataset(suite: dict, name: str, build: bool) -> Path:
    ds = suite["datasets"][name]
    path = expand(ds["path"])
    if path.exists():
        return path
    recipe = ds.get("build")
    if not build or not recipe:
        raise FileNotFoundError(f"dataset {name} missing at {path}; pass --build to create it from its recipe")
    model = suite.get("models", {}).get(recipe["model"], recipe["model"])
    cmd = [sys.executable, "-m", "ikharness.build_dataset", "--model", str(expand(model)),
           "--frames", str(recipe.get("frames", 30)), "--hips-mode", recipe.get("hips_mode", "absolute"),
           "--out", str(path)]
    for a in recipe["anims"]:
        base, clip = (a.rsplit(":", 1) if ":" in a and not Path(os.path.expanduser(a)).exists() else (a, None))
        cmd += ["--anim", str(expand(base)) + (f":{clip}" if clip else "")]
    if recipe.get("bone_map"):
        cmd += ["--bone-map", recipe["bone_map"]]
    if recipe.get("select"):
        cmd += ["--select", recipe["select"], "--oversample", str(recipe.get("oversample", 6))]
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"building dataset {name} ...", file=sys.stderr)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not path.exists():
        raise RuntimeError(f"dataset build failed for {name}:\n{(res.stdout + res.stderr)[-3000:]}")
    return path


def run_suite(suite_path: Path, ik: str, build: bool = False, out_dir: Optional[Path] = None,
              settle: Optional[int] = None, readout: str = "json", calibration: str = "rules") -> SuiteReport:
    suite = load_suite(suite_path)
    out_dir = out_dir or (ROOT / "out" / "suite")
    out_dir.mkdir(parents=True, exist_ok=True)
    settle = settle if settle is not None else int(suite.get("settle", 8))
    t0 = time.monotonic()
    entries: List[SuiteEntryResult] = []
    for e in suite["entries"]:
        ds_path = ensure_dataset(suite, e["dataset"], build)
        if readout == "xr":
            from .xr import run as run_xr
            report, _ = run_xr(ds_path, e["tracker_set"], ik=ik, out_dir=out_dir / "xr", log=lambda *_: None)
        else:
            report, _ = evaluate(ds_path, e["tracker_set"], ik=ik, settle=settle, out_dir=out_dir / "runs", readout=readout,
                                 calibration=calibration)
        direct = getattr(report, "json_readout", None)
        entries.append(SuiteEntryResult(
            dataset=e["dataset"], tracker_set=e["tracker_set"], weight=float(e.get("weight", 1.0)),
            body_score_deg=report.body_score_deg, weighted_score_deg=report.weighted_score_deg,
            end_effector_m=report.end_effector_position_mean_m, frames=report.frames_scored,
            json_readout_weighted_deg=direct.weighted_score_deg if direct is not None else None,
        ))
        print(f"  {e['dataset']}/{e['tracker_set']}: {report.weighted_score_deg:.2f} deg", file=sys.stderr)
    total_w = sum(x.weight for x in entries)
    final = sum(x.weight * x.weighted_score_deg for x in entries) / total_w if total_w else float("nan")
    if readout == "xr":
        calibration = "tpose"   # the demo always calibrates on the T-pose gesture
    rep = SuiteReport(suite=suite.get("name", suite_path.stem), implementation=ik, entries=entries,
                      final_deg=final, quality=quality_from_deg(final), seconds=time.monotonic() - t0,
                      readout=readout, calibration=calibration)
    tag = "" if (readout, calibration) == ("json", "rules") else f"_{readout}_{calibration}"
    (out_dir / f"{rep.suite}_{ik}{tag}.json").write_text(json.dumps(rep.to_dict(), indent=1))
    return rep
