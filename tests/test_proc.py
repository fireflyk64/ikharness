"""The process guard must kill runaway memory and enforce timeouts, and only its own children."""

import sys

from ikharness.proc import container_free_mb, run_guarded


def test_guard_passes_normal_commands():
    res = run_guarded([sys.executable, "-c", "print('ok')"], timeout=30, max_rss_mb=500, nice=0)
    assert res.returncode == 0 and res.stdout.strip() == "ok" and not res.killed


def test_guard_kills_memory_hog():
    hog = "import time\nx=[]\nwhile True:\n    x.append(bytearray(16*1024*1024)); time.sleep(0.05)\n"
    res = run_guarded([sys.executable, "-c", hog], timeout=60, max_rss_mb=200, nice=0)
    assert res.killed == "memory"
    assert res.peak_rss_mb < 400


def test_guard_enforces_timeout():
    res = run_guarded([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0, max_rss_mb=500, nice=0)
    assert res.killed == "timeout" and res.seconds < 10


def test_headroom_is_reported():
    free = container_free_mb()
    assert free is None or free > 0
