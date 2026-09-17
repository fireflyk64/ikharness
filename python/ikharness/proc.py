"""Run heavy external tools (Godot, monado-service) under a memory and time guard.

The development container has a hard memory limit; a runaway engine process takes the
whole machine down. :func:`run_guarded` starts the command in its own session, polls
the summed resident memory of every process in that session and kills the group when it
exceeds ``max_rss_mb`` or ``timeout``. :class:`Guard` does the same for a long-running
service started elsewhere.

    python -m ikharness.proc --max-rss-mb 3000 --timeout 120 -- godot --headless ...

prints the peak memory, which is how the per-step budgets in docs/resources.md were measured.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_MAX_RSS_MB = int(os.environ.get("IKH_MAX_RSS_MB", "3000"))
PAGE = os.sysconf("SC_PAGE_SIZE")


def session_rss_mb(sid: int) -> float:
    """Summed RSS (MB) of all processes whose session id is ``sid``."""
    total = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            stat = Path(f"/proc/{entry}/stat").read_text()
            # fields after the ")" of comm: state ppid pgrp session ...
            rest = stat[stat.rindex(")") + 2:].split()
            if int(rest[3]) != sid:
                continue
            statm = Path(f"/proc/{entry}/statm").read_text().split()
            total += int(statm[1]) * PAGE
        except (OSError, ValueError, IndexError):
            continue
    return total / (1024 * 1024)


def kill_session(pid: int, grace: float = 2.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)


@dataclass
class GuardedResult:
    returncode: int
    stdout: str
    stderr: str
    peak_rss_mb: float
    seconds: float
    killed: str = ""  # "", "memory" or "timeout"

    @property
    def log(self) -> str:
        return self.stdout + self.stderr


class MemoryLimitExceeded(RuntimeError):
    pass


class NoHeadroom(RuntimeError):
    pass


def container_free_mb() -> Optional[float]:
    """Free memory (MB) under the container's cgroup limit, or None when unlimited/unknown."""
    try:
        limit = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if limit == "max":
            return None
        current = int(Path("/sys/fs/cgroup/memory.current").read_text())
        # Page cache is reclaimable: count inactive file pages as free.
        inactive_file = 0
        for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines():
            if line.startswith("inactive_file "):
                inactive_file = int(line.split()[1])
        return (int(limit) - current + inactive_file) / (1024 * 1024)
    except (OSError, ValueError):
        return None


def ensure_headroom(min_free_mb: Optional[int] = None, wait: float = 60.0) -> Optional[float]:
    """Block until the container has ``min_free_mb`` free (other sessions share it), else raise."""
    min_free_mb = min_free_mb if min_free_mb is not None else int(os.environ.get("IKH_MIN_FREE_MB", "1200"))
    deadline = time.monotonic() + wait
    while True:
        free = container_free_mb()
        if free is None or free >= min_free_mb:
            return free
        if time.monotonic() > deadline:
            raise NoHeadroom(f"only {free:.0f} MB free in this container (need {min_free_mb}); "
                             "another session may be running heavy work. Set IKH_MIN_FREE_MB to override.")
        time.sleep(2.0)


def foreign_engine_processes() -> List[str]:
    """Godot / monado processes on this machine (any session), for status reports."""
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{entry}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        exe = cmd.split(" ", 1)[0].lower()
        if "godot" in exe or exe.endswith("monado-service"):
            out.append(f"{entry}: {cmd[:110]}")
    return out


def run_guarded(cmd: Sequence[str], timeout: float = 600.0, max_rss_mb: Optional[int] = None,
                env: Optional[dict] = None, cwd: Optional[str] = None, nice: int = 5) -> GuardedResult:
    """Run ``cmd`` to completion under the guard. Raises nothing; inspect ``killed``."""
    max_rss_mb = max_rss_mb or DEFAULT_MAX_RSS_MB
    ensure_headroom()
    t0 = time.monotonic()
    proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd,
                            start_new_session=True, preexec_fn=(lambda: os.nice(nice)) if nice else None)
    out: List[str] = []
    err: List[str] = []
    readers = [threading.Thread(target=lambda: out.append(proc.stdout.read()), daemon=True),
               threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)]
    for r in readers:
        r.start()
    peak = 0.0
    killed = ""
    while proc.poll() is None:
        rss = session_rss_mb(proc.pid)
        peak = max(peak, rss)
        if rss > max_rss_mb:
            killed = "memory"
        elif time.monotonic() - t0 > timeout:
            killed = "timeout"
        if killed:
            kill_session(proc.pid)
            break
        time.sleep(0.1)
    proc.wait()
    for r in readers:
        r.join(timeout=2)
    return GuardedResult(proc.returncode, "".join(out), "".join(err), peak, time.monotonic() - t0, killed)


class Guard:
    """Watch an already started process group (``Popen(..., start_new_session=True)``)."""

    def __init__(self, proc: subprocess.Popen, max_rss_mb: Optional[int] = None):
        self.proc = proc
        self.max_rss_mb = max_rss_mb or DEFAULT_MAX_RSS_MB
        self.peak_rss_mb = 0.0
        self.killed = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        while not self._stop.is_set() and self.proc.poll() is None:
            rss = session_rss_mb(self.proc.pid)
            self.peak_rss_mb = max(self.peak_rss_mb, rss)
            if rss > self.max_rss_mb:
                self.killed = "memory"
                kill_session(self.proc.pid)
                return
            self._stop.wait(0.2)

    def stop(self) -> None:
        self._stop.set()
        if self.proc.poll() is None:
            kill_session(self.proc.pid)
        self._thread.join(timeout=3)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--max-rss-mb", type=int, default=DEFAULT_MAX_RSS_MB)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--quiet", action="store_true", help="do not echo the command's output")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    res = run_guarded(cmd, timeout=args.timeout, max_rss_mb=args.max_rss_mb)
    if not args.quiet:
        sys.stdout.write(res.stdout[-4000:])
        sys.stderr.write(res.stderr[-4000:])
    print(f"[guard] rc={res.returncode} peak_rss={res.peak_rss_mb:.0f} MB time={res.seconds:.1f}s" + (f" KILLED ({res.killed})" if res.killed else ""))
    return res.returncode if not res.killed else 99


if __name__ == "__main__":
    sys.exit(main())
