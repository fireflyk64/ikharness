# ikharness documentation

One document per subproject. Each says what it is for, how to run it, and,
most importantly, **how to tell whether it is working** (the feedback loop).
The task list lives in [`../TODO.md`](../TODO.md).

| Document | Subproject | State |
|---|---|---|
| [getting-started.md](getting-started.md) | Install, use with your own animations, all three pipelines | current |
| [STATUS.md](STATUS.md) | What works, numbers, findings, limitations | 2026-09-17 |
| [monado-driver.md](monado-driver.md) | Headless Monado driver feeding trackers into OpenXR / OpenVR apps | working |
| [datasets.md](datasets.md) | Reference poses from animations, retargeting, limb lengths | working, retargeter pending |
| [scoring.md](scoring.md) | The metric, the single number, negative tests | working |
| [godot-harness.md](godot-harness.md) | Running IK implementations in Godot | working (RenIK, Godot built-ins, baseline) |
| [cli.md](cli.md) | The `ikh` driver command | working |
| [godot-openxr.md](godot-openxr.md) | Godot as an OpenXR client of the Monado driver | planned |
| [shadermotion.md](shadermotion.md) | Bone rotations as pixels: Godot port, capture, video | planned |
| [unity.md](unity.md) | Unity avatar, shader, harness | blocked (no Unity) |
| [resources.md](resources.md) | Memory guard, shared-machine rules, measured budgets | in force |

## The pipeline in one picture

```
 clips (.tres, glb, fbx, vrm)        godot/tools/export_poses.gd
        │  Godot retarget ─────────▶  dataset.json  (T-pose skeleton + limb lengths, sampled global bone poses)
        │                                   │
        │                     ikharness.trackers  (place virtual trackers on bones; 3pt … 11pt)
        │                                   │
        │              ┌────────────────────┼───────────────────────────┐
        ▼              ▼                    ▼                           ▼
  godot/harness   ikharness.replay     (future) Unity harness    (future) ShaderMotion capture
  RenIK / builtin  → Monado driver → OpenXR / OpenVR app                 → pixels → rotations
        │                                   │                           │
        └──────────────── result.json (solved global bone poses) ◀───────┘
                                            │
                              ikharness.scoring  (rest-relative angular error per bone)
                                            │
                                   ikh suite → one number
```

## Conventions that everything relies on

* **Skeleton**: Godot `SkeletonProfileHumanoid` bone names. Rest pose is a T-pose,
  +Y up, the character faces **+Z**, bone local +Y runs along the bone.
  `godot/tools/humanoid_profile.json` is the reference dump.
* **Poses in files are global** (skeleton space) transforms, quaternions `(x, y, z, w)`, meters.
* **Scores compare rest-relative rotations**: `delta = pose_rotation × rest_rotation⁻¹`, so two
  rigs with different bone axes but the same T-pose agree. Each result file carries its rig's
  rest so the scorer never assumes one.
* **OpenXR stage space faces -Z**; the replay tool applies a 180° yaw around +Y.
* **Trackers are rigid attachments to bones** (`trackers.py` is the single source of truth).
  A harness turns a tracker back into a bone target by undoing the offset, which is what a
  T-pose calibration with trackers placed exactly on the bones yields.

## Feedback loops, from fast to slow

Run them with `scripts/run_tests.sh` (parts, sequential, headroom reported); never start two
engine runs at once on the shared container ([resources.md](resources.md)).

1. `pytest tests/test_dataset_pipeline.py tests/test_protocol.py` — pure Python, seconds.
2. `pytest tests/test_godot_harness.py` — Godot headless on the mini dataset, ~10 s.
3. `pytest tests/test_monado_driver.py` — starts `monado-service`, OpenXR round trip, ~5 s.
4. `ikh negative --dataset ...` — the metric must degrade monotonically under perturbation.
5. `ikh suite` — the full number; compare against the table in `docs/scoring.md`.
