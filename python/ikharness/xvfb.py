"""Run a command on a private Xvfb display and reap everything afterwards.

    python -m ikharness.xvfb [--screen 320x240x24] [--fbdir DIR] [--display-file FILE] -- godot --display-driver x11 ...

``--fbdir DIR`` makes Xvfb keep its framebuffer in ``DIR/Xvfb_screen0`` (XWD format), which
another process can read at any time: that is how ``ikharness.screen`` takes the pixels off
the screen. ``--display-file FILE`` receives the display name (``:N``) once Xvfb is up.

``xvfb-run`` leaves its Xvfb orphaned for a moment; in a container whose PID 1 does not reap
orphans (ours is ``websockify``) every run then leaves a permanent zombie, and zombies count
against the container's process limit. Here Xvfb is a direct child that is terminated and
waited for, also when the wrapper itself receives SIGTERM from the memory guard.
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    screen = "320x240x24"
    fbdir = display_file = None
    while argv and argv[0] in ("--screen", "--fbdir", "--display-file") and len(argv) >= 2:
        if argv[0] == "--screen":
            screen = argv[1]
        elif argv[0] == "--fbdir":
            fbdir = argv[1]
        else:
            display_file = argv[1]
        argv = argv[2:]
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    if not shutil.which("Xvfb"):
        print("ikharness.xvfb: Xvfb is not installed (apt install xvfb)", file=sys.stderr)
        return 127

    r, w = os.pipe()
    xvfb_cmd = ["Xvfb", "-displayfd", str(w), "-screen", "0", screen, "-nolisten", "tcp"]
    if fbdir:
        os.makedirs(fbdir, exist_ok=True)
        xvfb_cmd += ["-fbdir", fbdir]
    xvfb = subprocess.Popen(xvfb_cmd, pass_fds=[w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    child = None

    def shutdown(*_):
        for p in (child, xvfb):
            if p is not None and p.poll() is None:
                p.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        ready, _, _ = select.select([r], [], [], 20.0)
        display = os.read(r, 32).decode().strip() if ready else ""
        os.close(r)
        if not display:
            print("ikharness.xvfb: Xvfb did not report a display", file=sys.stderr)
            return 1
        env = dict(os.environ, DISPLAY=f":{display}")
        if display_file:
            with open(display_file, "w") as f:
                f.write(f":{display}\n")
        env.pop("WAYLAND_DISPLAY", None)
        child = subprocess.Popen(argv, env=env)
        return child.wait()
    finally:
        shutdown()
        for p in (child, xvfb):
            if p is not None:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()


if __name__ == "__main__":
    sys.exit(main())
