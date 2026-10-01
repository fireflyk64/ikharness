# ikharness

An objective test harness for **inverse kinematics in virtual reality
avatars**. Known-good animations are turned into tracker poses, fed to an IK
implementation, and the resulting bone rotations are scored against the
original animation. The goal is a single, comparable score per implementation
(Godot, Unity, VRChat, ...) and per tracker configuration (3-point, 6-point,
11-point, ...).

**Start here:** [docs/getting-started.md](docs/getting-started.md) (install, your own FBX/GLB clips, Godot / ShaderMotion / Monado pipelines) · [docs/STATUS.md](docs/STATUS.md) (what works, numbers, findings) · [TODO.md](TODO.md).

## Pipeline

```
 animation sources ──▶ reference poses ──▶ virtual trackers ──▶ IK under test ──▶ bone rotations ──▶ score
 (FBX, VMD, BVH,        (sampled frames,     (head, hands, hips,   (engine harness      (read back from      (per-joint
  GLB, tracker logs)     bone lengths)        feet, elbows, ...)    or runtime driver)    the application)     angular error)
```

## Components

| Component | Status | Where |
|---|---|---|
| Headless Monado driver: HMD + controllers (poses, triggers, buttons) + N trackers driven over a socket | **working, tested** | [`monado/`](monado/README.md) |
| Monado patches: square per-eye projection, `XR_HTCX_vive_tracker_interaction` (tracker roles for Godot / Unity), SteamVR plugin trackers | working; plugin tested in a mock vrserver | `monado/patches/` |
| Reference pose datasets: Godot exporter for GLB/FBX/VRM, retargeting through Godot's importer, pose-diversity frame selection, export as GLB for other engines | **working, tested** | `godot/tools/`, [`docs/datasets.md`](docs/datasets.md) |
| Virtual trackers (3 to 11 points), T-pose calibration, rest-relative scoring, suites (one number), negative tests, reports | **working, tested** | `python/ikharness/`, [`docs/scoring.md`](docs/scoring.md) |
| Godot harness with RenIK and Godot built-in IK adapters | **working, tested** | [`godot/harness/`](docs/godot-harness.md) |
| ShaderMotion: codec, Godot CPU encoder, recorder mesh + shader, decode from screenshots and video | **working, tested** | [`docs/shadermotion.md`](docs/shadermotion.md) |
| `ikh xr`: a Godot OpenXR application on Monado, calibrated by pulling both triggers in a T-pose, scored from screen pixels | **working, tested** | [`docs/godot-openxr.md`](docs/godot-openxr.md) |
| Unity harness, avatar and shader; VRChat | blocked (Unity / SteamVR not installed) | [`docs/unity.md`](docs/unity.md) |

Current numbers (default suite, weighted degrees, lower is better): Godot built-in IK 15.25,
RenIK 19.80, rest pose 69.74; through rendered pixels and the whole OpenXR chain the two
solvers score 20.6 and 25.6 (the pixel format adds its own floor). Details in
[`docs/STATUS.md`](docs/STATUS.md), tasks in [`TODO.md`](TODO.md).

## Quick start

```sh
scripts/setup.sh --apt              # venv, pinned deps, Godot 4.7.2, data repos (add --with-monado for the runtime)
scripts/ikh status
scripts/ikh suite --ik builtin --build                                    # the single number
scripts/ikh xr --dataset out/datasets/vsk_walk.json --tracker-set 6pt     # the whole OpenXR chain, read off the screen (needs --with-monado)
scripts/ikh report                                                        # compare what has been run
```

Full instructions: [docs/getting-started.md](docs/getting-started.md).

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

Current suite numbers (`ikh suite`, `suites/default.json`: V-Sekai idle/walk and 46 s mocap on the
V-Sekai avatar, a Perfume dance (BVH-named rig) and an MMD dance (VRM rig) retargeted through Godot; 6 and
11 point tracking; weighted mean angular error over 21 body bones, lower is better; `quality` is
`100·exp(-deg/25)`):

| Implementation | final (deg) | quality | walk 6pt | walk 11pt | mocap 6pt | mocap 11pt | perfume 6pt | perfume 11pt | mmd 6pt | mmd 11pt |
|---|---|---|---|---|---|---|---|---|---|---|
| builtin (Godot TwoBoneIK3D + FABRIK3D) | **16.18** | 52.4 | 12.84 | 11.24 | 15.35 | 13.46 | 27.35 | 16.58 | 15.04 | 11.66 |
| renik | **22.10** | 41.3 | 14.26 | 12.33 | 21.36 | 21.87 | 39.54 | 25.03 | 18.75 | 18.11 |
| none (rest pose) | **69.74** | 6.1 | 39.25 | 39.25 | 58.26 | 58.26 | 133.08 | 133.08 | 48.37 | 48.37 |

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
