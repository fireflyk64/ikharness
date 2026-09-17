# Resources and running safely on a shared machine

The development container is small (8 GB memory limit, 2 cores) and **shared**: other
sessions run their own Godot jobs in it. On 2026-09-15 a full test run coincided with that
load, the container ran out of memory and restarted. Rules since then:

1. **Never kill processes you did not start.** `ikharness.proc` only signals the process
   group it created. The only by-name kill in use is `pkill -x monado-service` for our own
   service. Other sessions' Godot processes are off limits.
2. **Every engine launch is guarded.** `run_guarded()` starts Godot / Monado in its own
   session, niced, sums the resident memory of that session every 100 ms and kills it above
   `IKH_MAX_RSS_MB` (default 3000) or on timeout. `Guard` does the same for the
   `monado-service` started by the test fixture.
3. **Headroom first.** `ensure_headroom()` waits up to 60 s for `IKH_MIN_FREE_MB` (default
   1200) free under the cgroup limit, counting reclaimable page cache, and refuses to start
   otherwise.
4. **One heavy step at a time.** No parallel Godot / Monado runs; `scripts/run_tests.sh` runs
   the test suite in parts with a headroom report between them.
5. **Do not peg the cores.** The harness caps its frame rate (`--max-fps`, default 240).

## Measured peaks (2026-09-16, this container)

| Step | Peak RSS | Time |
|---|---|---|
| Godot harness run, 45 frames × 8 settle | 109 MB | 8 s |
| Godot editor import for retargeting (Perfume GLB) | 664 MB | 25 s |
| `monado-service`, null compositor, idle | 75 MB | — |
| Godot with software OpenGL (llvmpipe) under Xvfb, ShaderMotion recorder, 3 frames | 337 MB | 8 s |
| Python-only tests | < 100 MB | 2 s |

None of these is large on its own; the risk is concurrency with other work. Measure a new
step before adopting it:

```sh
.venv/bin/python -m ikharness.proc --max-rss-mb 3000 --timeout 300 -- godot --headless ...
# [guard] rc=0 peak_rss=664 MB time=24.9s
```

## Feedback loop

* `ikh status` prints container headroom and any Godot / Monado processes on the machine
  (from any session) before you start something heavy.
* `tests/test_proc.py` proves the guard kills a deliberate memory hog.
