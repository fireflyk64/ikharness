"""``ikh``: one command to drive the IK harness.

    ikh status
    ikh dataset build|info ...
    ikh eval --dataset D --tracker-set 6pt --ik renik [--perturb NAME:MAG]
    ikh suite --ik renik [--build]
    ikh negative --dataset D --ik renik
    ikh score --dataset D --result R [--through-shadermotion]
    ikh shadermotion encode|decode ...
    ikh service | probe | replay | devices | pose | drop | ping
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def cmd_status(args) -> int:
    ok = True

    def line(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '--'}] {label:<28} {detail}")

    godot = os.environ.get("GODOT") or shutil.which("godot") or str(Path.home() / ".local/bin/godot")
    line("godot", Path(godot).exists(), godot)
    line("harness class cache", (ROOT / "godot/harness/.godot/global_script_class_cache.cfg").exists(),
         "run: godot --headless --path godot/harness --import" if not (ROOT / "godot/harness/.godot").exists() else "")
    prefix = Path(os.environ.get("MONADO_PREFIX", Path.home() / ".local/monado-ikharness"))
    line("monado-service", (prefix / "bin/monado-service").exists(), str(prefix))
    rt = os.environ.get("XR_RUNTIME_JSON", str(prefix / "share/openxr/1/openxr_monado.json"))
    line("XR_RUNTIME_JSON", Path(rt).exists(), rt)
    port = int(os.environ.get("IKH_PORT", "4343"))
    s = socket.socket()
    s.settimeout(0.3)
    running = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    print(f"  [{'ok' if running else '..'}] {'driver listening':<28} 127.0.0.1:{port}{'' if running else ' (start with: ikh service)'}")
    ds = sorted((ROOT / "out/datasets").glob("*.json")) if (ROOT / "out/datasets").exists() else []
    print(f"  [{'ok' if ds else '..'}] {'datasets':<28} {', '.join(p.stem for p in ds) or 'none (ikh suite --build)'}")
    from .proc import DEFAULT_MAX_RSS_MB, container_free_mb, foreign_engine_processes
    free = container_free_mb()
    if free is not None:
        print(f"  [{'ok' if free > 1200 else '!!'}] {'container memory free':<28} {free:.0f} MB (guard kills a step above {DEFAULT_MAX_RSS_MB} MB)")
    others = foreign_engine_processes()
    for o in others[:4]:
        print(f"  [..] {'engine process running':<28} {o}")
    try:
        import xr  # noqa: F401
        line("pyopenxr", True)
    except Exception as e:  # pragma: no cover
        line("pyopenxr", False, str(e))
    return 0 if ok else 1


def cmd_dataset(args) -> int:
    if args.dataset_cmd == "build":
        from .build_dataset import main
        return main(args.rest)
    if args.dataset_cmd == "info":
        import numpy as np
        from .dataset import Dataset
        d = Dataset.load(args.path)
        sk = d.skeleton
        print(f"{args.path}: {len(d.frames)} frames from {len(d.sources)} clip(s), {len(sk.order)} bones, model {sk.source_model}")
        print(f"  hips {sk.hips_height:.3f} m, head {sk.head_height:.3f} m, eyes {sk.eye_height:.3f} m")
        ll = sk.limb_lengths()
        print("  limb lengths (m): " + ", ".join(f"{k} {v:.3f}" for k, v in ll.items() if v > 0))
        foot = np.array([min(f.bones["LeftFoot"].position[1], f.bones["RightFoot"].position[1]) for f in d.frames])
        hips = np.array([f.bones["Hips"].position[1] for f in d.frames])
        rest_foot = sk.bones["LeftFoot"].rest_global.position[1]
        print(f"  lowest foot y: p10 {np.percentile(foot, 10):.3f} p50 {np.percentile(foot, 50):.3f} (rest {rest_foot:.3f})")
        print(f"  hips y: p50 {np.percentile(hips, 50):.3f} p90 {np.percentile(hips, 90):.3f} (rest {sk.hips_height:.3f})")
        from .retarget import rest_conformance
        dev = rest_conformance(sk)
        worst = sorted(dev.items(), key=lambda kv: -kv[1])[:3]
        print(f"  rest vs humanoid profile: max {worst[0][1]:.2f} deg ({worst[0][0]})" + ("" if worst[0][1] < 0.5 else "  <-- NOT in profile convention"))
        for s in d.sources:
            print(f"  clip {s.get('id')}: {Path(str(s.get('path'))).name} '{s.get('animation')}' {float(s.get('length', 0)):.1f}s hips_mode={s.get('hips_mode')}")
        return 0
    return 1


def cmd_eval(args) -> int:
    from .run_godot import main
    return main(args.rest)


def cmd_suite(args) -> int:
    from .suite import run_suite
    rep = run_suite(Path(args.suite), args.ik, build=args.build, settle=args.settle)
    print(rep.summary())
    return 0


def cmd_negative(args) -> int:
    from .negative import DEFAULT_TRACKER_LADDERS, LadderStep, is_monotonic
    from .run_godot import evaluate

    dataset = Path(args.dataset)
    ladders = DEFAULT_TRACKER_LADDERS if not args.ladder else {args.ladder: DEFAULT_TRACKER_LADDERS[args.ladder]}
    base, _ = evaluate(dataset, args.tracker_set, ik=args.ik, settle=args.settle, out_dir=ROOT / "out/negative")
    print(f"baseline {args.ik}/{args.tracker_set}: {base.weighted_score_deg:.2f} deg")
    failed = []
    for name, specs in ladders.items():
        steps = [LadderStep("none", base.weighted_score_deg, base.end_effector_position_mean_m)]
        for spec in specs:
            rep, _ = evaluate(dataset, args.tracker_set, ik=args.ik, settle=args.settle, out_dir=ROOT / "out/negative", perturb=spec)
            steps.append(LadderStep(spec, rep.weighted_score_deg, rep.end_effector_position_mean_m))
        mono = is_monotonic(steps)
        print(f"{'ok' if mono else 'FAIL'} {name:<14} " + "  ->  ".join(f"{s.spec}={s.score_deg:.2f}" for s in steps))
        if not mono:
            failed.append(name)
    if failed:
        print(f"non-monotonic ladders: {failed}")
        return 1
    print("all ladders degrade monotonically")
    return 0


def cmd_score(args) -> int:
    """Score any result file (from any engine or a ShaderMotion decode) against a dataset."""
    from .dataset import Dataset
    from .scoring import score
    from .testfile import HarnessResult

    dataset = Dataset.load(args.dataset)
    result = HarnessResult.load(args.result)
    if args.through_shadermotion:
        from .shadermotion.readout import roundtrip_dataset
        dataset = roundtrip_dataset(dataset, propagate_leftovers=not args.no_leftovers)
    frames = list(result.frames)
    if len(frames) < len(dataset.frames):
        frames += [None] * (len(dataset.frames) - len(frames))
    report = score(dataset, frames[: len(dataset.frames)], implementation=args.implementation or result.implementation,
                   tracker_set=result.tracker_set or "-", result_rest=result.rest or None)
    if args.out:
        report.save(args.out)
    print(report.summary())
    return 0


def cmd_service(args) -> int:
    script = ROOT / "monado/scripts/run_service.sh"
    os.execv("/bin/bash", ["bash", str(script)] + args.rest)


def cmd_replay(args) -> int:
    from .replay import main
    return main(args.rest)


def cmd_driver(args) -> int:
    from .cli import main
    return main([args.driver_cmd] + args.rest)


PASS_THROUGH = {
    "eval": "ikharness.run_godot",
    "replay": "ikharness.replay",
    "shadermotion": "ikharness.shadermotion.cli",
}
DRIVER_CMDS = ("probe", "devices", "ping", "pose", "drop")


def _pass_through(argv) -> int:
    """Commands whose flags belong to another module are forwarded verbatim."""
    import importlib
    if not argv:
        return -1
    cmd = argv[0]
    if cmd in PASS_THROUGH and "-h" not in argv[1:] and "--help" not in argv[1:] or (cmd in PASS_THROUGH and len(argv) > 1):
        return importlib.import_module(PASS_THROUGH[cmd]).main(argv[1:])
    if cmd == "dataset" and len(argv) > 1 and argv[1] == "build":
        from .build_dataset import main as build_main
        return build_main(argv[2:])
    if cmd == "service":
        script = ROOT / "monado/scripts/run_service.sh"
        os.execv("/bin/bash", ["bash", str(script)] + argv[1:])
    if cmd in DRIVER_CMDS:
        from .cli import main as driver_main
        return driver_main(list(argv))
    return -1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rc = _pass_through(argv)
    if rc != -1:
        return rc
    p = argparse.ArgumentParser(prog="ikh", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="what is installed, built and running").set_defaults(fn=cmd_status)
    sub.add_parser("shadermotion", help="encode reference frames to images / decode images to results (see --help)")

    d = sub.add_parser("dataset", help="build or inspect reference datasets")
    dsub = d.add_subparsers(dest="dataset_cmd", required=True)
    b = dsub.add_parser("build", help="export clips with Godot (see ikharness.build_dataset --help)")
    b.add_argument("rest", nargs=argparse.REMAINDER)
    i = dsub.add_parser("info", help="print skeleton, limb lengths and ground contact stats")
    i.add_argument("path")
    d.set_defaults(fn=cmd_dataset)

    e = sub.add_parser("eval", help="run one dataset through a harness and score it (see ikharness.run_godot --help)")
    e.add_argument("rest", nargs=argparse.REMAINDER)
    e.set_defaults(fn=cmd_eval)

    s = sub.add_parser("suite", help="run a suite and print the single number")
    s.add_argument("--suite", default=str(ROOT / "suites/default.json"))
    s.add_argument("--ik", default="renik")
    s.add_argument("--build", action="store_true", help="build missing datasets from their recipes")
    s.add_argument("--settle", type=int, default=None)
    s.set_defaults(fn=cmd_suite)

    n = sub.add_parser("negative", help="perturb tracker inputs and check the score degrades")
    n.add_argument("--dataset", required=True)
    n.add_argument("--tracker-set", default="6pt")
    n.add_argument("--ik", default="renik")
    n.add_argument("--settle", type=int, default=8)
    n.add_argument("--ladder", default=None, help="only this ladder (hands_offset, head_yaw, feet_offset, noise)")
    n.set_defaults(fn=cmd_negative)

    sc = sub.add_parser("score", help="score a result file from any source against a dataset")
    sc.add_argument("--dataset", required=True)
    sc.add_argument("--result", required=True, help="ikharness-result/1 JSON (harness output or `ikh shadermotion decode`)")
    sc.add_argument("--through-shadermotion", action="store_true", help="pass the reference through ShaderMotion first so the format's floor cancels")
    sc.add_argument("--no-leftovers", action="store_true", help="with --through-shadermotion: shader-style projection")
    sc.add_argument("--implementation", default=None)
    sc.add_argument("--out", default=None, help="write the score JSON here")
    sc.set_defaults(fn=cmd_score)

    sv = sub.add_parser("service", help="start monado-service headless with the driver (foreground)")
    sv.add_argument("rest", nargs=argparse.REMAINDER)
    sv.set_defaults(fn=cmd_service)

    r = sub.add_parser("replay", help="stream a dataset's trackers into the Monado driver (see ikharness.replay --help)")
    r.add_argument("rest", nargs=argparse.REMAINDER)
    r.set_defaults(fn=cmd_replay)

    for name, help_ in [("probe", "read poses back through OpenXR"), ("devices", "list driver devices"),
                        ("ping", "ping the driver"), ("pose", "set a device pose: DEV x y z [qx qy qz qw]"),
                        ("drop", "mark a device disconnected")]:
        c = sub.add_parser(name, help=help_)
        c.add_argument("rest", nargs=argparse.REMAINDER)
        c.set_defaults(fn=cmd_driver, driver_cmd=name)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
