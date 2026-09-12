"""Command line helpers for poking the Monado ikharness driver.

    python -m ikharness.cli devices                 # list devices from HELLO
    python -m ikharness.cli ping
    python -m ikharness.cli pose waist 0 0.95 0     # position only (identity rotation)
    python -m ikharness.cli pose hmd 0 1.6 0 0 0.707 0 0.707   # with quaternion x y z w
    python -m ikharness.cli drop waist              # mark a device disconnected
    python -m ikharness.cli probe                   # read poses back through OpenXR (needs XR_RUNTIME_JSON)
"""

from __future__ import annotations

import argparse
import sys

from .protocol import DEFAULT_HOST, DEFAULT_PORT, IkhClient, Pose


def _client(args) -> IkhClient:
    return IkhClient(args.host, args.port)


def cmd_devices(args) -> int:
    with _client(args) as c:
        print(f"{'idx':>3} {'kind':<17} {'name':<16} serial")
        for d in c.devices:
            print(f"{d.index:>3} {d.kind.name:<17} {d.name:<16} {d.serial}")
    return 0


def cmd_ping(args) -> int:
    with _client(args) as c:
        ack = c.ping()
        print(f"last frame {ack.frame_id} applied at {ack.applied_at_ns} ns")
    return 0


def cmd_pose(args) -> int:
    q = tuple(args.values[3:7]) if len(args.values) >= 7 else (0.0, 0.0, 0.0, 1.0)
    pose = Pose(position=tuple(args.values[0:3]), orientation=q)
    with _client(args) as c:
        ack = c.send_frame({args.device: pose}, frame_id=args.frame_id)
        print(f"frame {ack.frame_id} applied at {ack.applied_at_ns} ns")
    return 0


def cmd_drop(args) -> int:
    with _client(args) as c:
        ack = c.send_frame({args.device: Pose.disconnected()}, frame_id=args.frame_id)
        print(f"frame {ack.frame_id} applied at {ack.applied_at_ns} ns")
    return 0


def cmd_probe(args) -> int:
    from .openxr_session import HeadlessSession, pose_to_tuple
    from .xdev_space import EXTENSION_NAME, XDevList

    with HeadlessSession(extra_extensions=[EXTENSION_NAME]) as s:
        t = s.now()
        s.sync_actions()

        def show(label, loc):
            pos, rot = pose_to_tuple(loc.pose)
            print(f"{label:<28} flags={loc.location_flags:#04x} pos=({pos[0]:+.4f}, {pos[1]:+.4f}, {pos[2]:+.4f}) "
                  f"rot=({rot[0]:+.4f}, {rot[1]:+.4f}, {rot[2]:+.4f}, {rot[3]:+.4f})")

        show("view (hmd)", s.locate(s.view, t))
        for hand in ("left", "right"):
            print(f"{hand} profile: {s.current_interaction_profile(hand) or '(none)'}")
            show(f"{hand} grip", s.locate(s.grip_spaces[hand], t))
        with XDevList(s.session) as xdl:
            for d in xdl.devices():
                if not d.can_create_space:
                    print(f"{d.serial:<28} (no space)  {d.name}")
                    continue
                sp = xdl.create_space(d)
                show(d.serial, s.locate(sp, t))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ikharness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices").set_defaults(fn=cmd_devices)
    sub.add_parser("ping").set_defaults(fn=cmd_ping)

    sp = sub.add_parser("pose")
    sp.add_argument("device")
    sp.add_argument("values", type=float, nargs="+", help="x y z [qx qy qz qw]")
    sp.add_argument("--frame-id", type=int, default=0)
    sp.set_defaults(fn=cmd_pose)

    sp = sub.add_parser("drop")
    sp.add_argument("device")
    sp.add_argument("--frame-id", type=int, default=0)
    sp.set_defaults(fn=cmd_drop)

    sub.add_parser("probe").set_defaults(fn=cmd_probe)

    args = p.parse_args(argv)
    if args.cmd == "pose" and len(args.values) not in (3, 7):
        p.error("pose needs 3 or 7 values")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
