"""Replay a reference dataset's virtual trackers through the Monado ikharness driver.

    python -m ikharness.replay --dataset out/datasets/vsk_walk.json --tracker-set 6pt \
        [--dwell 1.0] [--loop] [--host 127.0.0.1 --port 4343] [--verify]

Each frame's trackers are converted from dataset space (character faces +Z)
to the OpenXR stage space (faces -Z) and sent as one FRAME, then held for
``--dwell`` seconds so the application under test can settle. Roles map to
driver devices by name: ``head`` -> the HMD, ``left_hand``/``right_hand`` -> the
controllers, everything else -> the tracker with that role.

``--verify`` additionally opens a headless OpenXR session and checks that the
runtime reports the head, hands and trackers at the sent poses, which proves
the whole path from dataset to runtime.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Dict

import numpy as np

from .dataset import Dataset
from .mathutil import Transform, quat_angle
from .protocol import DEFAULT_HOST, DEFAULT_PORT, IkhClient, Pose
from .testfile import build_test_file
from .trackers import TRACKER_SETS, place_trackers, rules_for, to_openxr_stage

ROLE_TO_DEVICE = {"head": "hmd", "left_hand": "left_hand", "right_hand": "right_hand"}


def frame_poses(dataset: Dataset, index: int, roles, rules) -> Dict[str, Pose]:
    trackers = place_trackers(dataset.frames[index], dataset.skeleton, roles, rules)
    out: Dict[str, Pose] = {}
    for role, t in trackers.items():
        s = to_openxr_stage(t)
        out[ROLE_TO_DEVICE.get(role, role)] = Pose(tuple(float(v) for v in s.position), tuple(float(v) for v in s.rotation))
    return out


def verify_through_openxr(client: IkhClient, sent: Dict[str, Pose]) -> float:
    """Return the largest position error (m) between sent poses and what OpenXR reports."""
    from .openxr_session import HeadlessSession
    from .xdev_space import EXTENSION_NAME, XDevList

    worst = 0.0
    with HeadlessSession(extra_extensions=[EXTENSION_NAME]) as s:
        t = s.now()
        s.sync_actions()
        checks = {}
        loc = s.locate(s.view, t)
        checks["hmd"] = loc
        for hand in ("left", "right"):
            checks[f"{hand}_hand"] = s.locate(s.grip_spaces[hand], t)
        with XDevList(s.session) as xdl:
            for name, pose in sent.items():
                if name in checks:
                    continue
                dev = xdl.find_by_serial(client.device(name).serial)
                checks[name] = s.locate(xdl.create_space(dev), t)
        for name, pose in sent.items():
            got = checks[name].pose
            err = math.dist((got.position.x, got.position.y, got.position.z), pose.position)
            worst = max(worst, err)
    return worst


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--tracker-set", default="6pt")
    p.add_argument("--dwell", type=float, default=1.0, help="seconds to hold each frame")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--verify", action="store_true", help="read poses back through OpenXR after each frame")
    p.add_argument("--frames", type=int, default=0, help="only replay the first N frames")
    args = p.parse_args(argv)

    dataset = Dataset.load(args.dataset)
    roles = TRACKER_SETS[args.tracker_set] if args.tracker_set in TRACKER_SETS else args.tracker_set.split(",")
    rules = rules_for(dataset.skeleton)
    count = len(dataset.frames) if args.frames <= 0 else min(args.frames, len(dataset.frames))

    with IkhClient(args.host, args.port) as client:
        missing = [ROLE_TO_DEVICE.get(r, r) for r in roles if ROLE_TO_DEVICE.get(r, r) not in client.device_names]
        if missing:
            print(f"driver does not publish devices for: {missing} (have {client.device_names})", file=sys.stderr)
            return 2
        # Devices outside the tracker set are reported as disconnected so the app sees the right set.
        unused = {d.name: Pose.disconnected() for d in client.devices if d.name not in {ROLE_TO_DEVICE.get(r, r) for r in roles}}
        frame_id = 0
        while True:
            for i in range(count):
                poses = frame_poses(dataset, i, roles, rules)
                poses.update(unused)
                frame_id += 1
                ack = client.send_frame(poses, frame_id=frame_id)
                msg = f"frame {i} (id {ack.frame_id}) sent"
                if args.verify:
                    worst = verify_through_openxr(client, {k: v for k, v in poses.items() if k not in unused})
                    msg += f", OpenXR round trip max error {worst * 1000:.2f} mm"
                print(msg)
                time.sleep(args.dwell)
            if not args.loop:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
