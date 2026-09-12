# ikharness

An objective test harness for **inverse kinematics in virtual reality
avatars**. Known-good animations are turned into tracker poses, fed to an IK
implementation, and the resulting bone rotations are scored against the
original animation. The goal is a single, comparable score per implementation
(Godot, Unity, VRChat, ...) and per tracker configuration (3-point, 6-point,
11-point, ...).

## Pipeline

```
 animation sources ──▶ reference poses ──▶ virtual trackers ──▶ IK under test ──▶ bone rotations ──▶ score
 (FBX, VMD, BVH,        (sampled frames,     (head, hands, hips,   (engine harness      (read back from      (per-joint
  GLB, tracker logs)     bone lengths)        feet, elbows, ...)    or runtime driver)    the application)     angular error)
```

## Components

| Component | Status | Where |
|---|---|---|
| Headless Monado driver: HMD + controllers + N trackers driven over a socket, for closed-source / runtime-level tests | **working, tested** | [`monado/`](monado/README.md) |
| Python protocol client, headless OpenXR probe, xdev-space bindings, dataset replay into the runtime | working | `python/ikharness/` |
| Reference pose datasets: Godot-based exporter for GLB/FBX/VRM models with `.tres` or embedded clips, bone lengths recorded | **working** | [`godot/tools/`](godot/README.md), `ikharness.build_dataset` |
| Virtual tracker placement (3 to 11 point sets), scoring (per-bone angular error, weighted body score) | **working, tested** | `python/ikharness/trackers.py`, `scoring.py` |
| Godot harness (GDScript) with a RenIK adapter and a no-IK baseline | **working, tested** | [`godot/harness/`](godot/README.md), `ikharness.run_godot` |
| BVH and VMD importers | planned (BVH clips also exist as GLB; the VMD sample ships a converted clip) | |
| Unity harness (C#) | planned | `unity/` |
| Bone-to-pixel avatar shader + frame capture for closed-source apps | planned | `capture/` |
| Multi-run comparison reports | planned | |

## Quick start (Monado driver)

```sh
monado/scripts/setup_monado.sh                    # clone + patch + build + install Monado with the driver
monado/scripts/run_service.sh &                   # headless runtime, listening on 127.0.0.1:4343
python3 -m venv .venv && .venv/bin/pip install -e '.[openxr,test]'
export XR_RUNTIME_JSON=~/.local/monado-ikharness/share/openxr/1/openxr_monado.json
.venv/bin/python -m ikharness.cli devices         # what the driver publishes
.venv/bin/python -m ikharness.cli pose waist 0 0.95 0
.venv/bin/python -m ikharness.cli probe           # read every pose back through OpenXR
.venv/bin/pytest -v                               # end-to-end test (starts its own service)
```

## Quick start (dataset, Godot harness, score)

```sh
export GODOT=~/.local/bin/godot                   # Godot 4.7 headless binary
godot --headless --path godot/harness --import    # once: build the harness project's class cache
.venv/bin/python -m ikharness.build_dataset --model avatar.glb --anim walk.tres --frames 15 \
    --hips-mode ratio:0.995 --out out/datasets/walk.json
.venv/bin/python -m ikharness.run_godot --dataset out/datasets/walk.json --tracker-set 6pt --ik renik
.venv/bin/python -m ikharness.replay --dataset out/datasets/walk.json --tracker-set 6pt --verify   # into Monado
```

First numbers (V-Sekai idle/walk clips on the V-Sekai test avatar, 45 frames,
mean angular error over 21 body bones):

| Implementation | Trackers | Body score | Weighted | End effectors |
|---|---|---|---|---|
| RenIK (Godot) | 11 point | 11.2 deg | 12.3 deg | 0.5 cm |
| RenIK (Godot) | 6 point | 13.2 deg | 14.3 deg | 0.4 cm |
| none (rest pose) | 6 point | 38.3 deg | 39.3 deg | 36.5 cm |

## Conventions

* Poses are in OpenXR / Godot coordinates: right handed, +Y up, -Z forward,
  meters, quaternions `(x, y, z, w)`, expressed in the STAGE space (floor
  origin). Unity harnesses convert to left handed on their side.
* Tracker roles use the SteamVR / Vive tracker vocabulary: `waist`, `chest`,
  `left_foot`, `right_foot`, `left_knee`, `right_knee`, `left_elbow`,
  `right_elbow`, `left_shoulder`, `right_shoulder`.
* The driver never interprets poses. Where a controller sits relative to the
  hand bone, or a tracker relative to the foot, is the orchestrator's job, so
  the same reference frame can be replayed against every implementation.

## License

BSD-2-Clause, see [LICENSE](LICENSE). Monado itself is BSL-1.0; only a small
registration patch touches its tree.
