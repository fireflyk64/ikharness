# Getting started: run the harness with your own animations

This is the end-to-end, copy-paste guide. Every command is run from the repository root
and uses `scripts/ikh`, the single entry point (`docs/cli.md` lists all subcommands).

```
your clip (FBX / GLB / VRM / Godot .tres)
   │  1. dataset build        (Godot, headless; retargets foreign rigs onto the standard humanoid)
   ▼
out/datasets/<name>.json      reference poses + skeleton + limb lengths
   │  2. eval / suite         (Godot harness solves IK from virtual trackers, scorer compares)
   ▼
out/results/…, out/suite/…    per-bone errors, one number per implementation
   │  3. readouts             JSON | ShaderMotion pixels (CPU) | ShaderMotion pixels (rendered)
   │  4. Monado               replay the same trackers into an OpenXR / OpenVR runtime
```

## 1. Install

Linux (tested on Ubuntu 26.04, 2 cores, 8 GB). Godot 4.7 and Python 3.10+ are required;
Monado is optional (only for driving external XR applications).

```sh
git clone git@github.com:fireflyk64/ikharness.git && cd ikharness
scripts/setup.sh --apt              # apt packages (sudo), venv, pinned Python deps, Godot 4.7.2, class cache, data repos
scripts/setup.sh --apt --with-monado   # ... plus the Monado runtime with the ikharness driver (~20 min build)
scripts/ikh status                  # every line should say ok (except "driver listening" until you start it)
```

`scripts/setup.sh` is idempotent. It pins Godot to `godot/GODOT_VERSION`, Python packages to
`requirements-lock.txt`, Monado to `monado/MONADO_COMMIT` and the animation repositories to
the commits in `data/SOURCES.md` (cloned into `~/dev/animations`, or `$IKH_DATA_DIR`).

Reproduce the published numbers:

```sh
scripts/ikh suite --ik builtin --build     # builds the four datasets from their recipes, then scores
scripts/ikh suite --ik renik
scripts/ikh suite --ik none
```

Expected (`docs/scoring.md` keeps the current table): builtin ≈ 16.2°, renik ≈ 22.1°, rest
pose ≈ 69.7°. Runs are deterministic; differences beyond 0.05° mean something changed.

## 2. Bring your own animation

### 2a. What the exporter needs

* A **model** with a skeleton: GLB/glTF, FBX, or a VRM saved as GLB. The skeleton must use
  Godot's humanoid bone names (`Hips`, `Spine`, `LeftUpperArm`, ...), **or** you give a bone
  map and the importer renames and re-poses it (T-pose, profile axes) for you.
* One or more **clips**: a Godot `Animation` (`.tres`/`.res`) whose tracks are
  `<anything>:<BoneName>`, or clips embedded in the GLB/FBX itself.

Find out what a file contains:

```sh
godot --headless --path godot/tools -s inspect_model.gd -- /abs/path/model.glb
```

It prints every skeleton with its bone names and rest orientations, and every animation
clip with its length and track count.

### 2b. Rig already uses humanoid names (VRM exports from Godot, V-Sekai avatars)

```sh
scripts/ikh dataset build --model avatar.glb --anim avatar.glb --frames 40 \
    --hips-mode absolute --out out/datasets/mine.json
scripts/ikh dataset build --model avatar.glb --anim clipA.tres --anim clipB.tres --frames 15 \
    --hips-mode ratio:0.995 --out out/datasets/mine.json
scripts/ikh dataset info out/datasets/mine.json
```

* `--anim file.glb` uses the first non-RESET clip; `--anim "file.glb:Clip Name"` picks one.
* `--frames N` samples N frames uniformly over the clip (edges trimmed by `--start`/`--end`).
* `--hips-mode` decides how hips *position* keys land on your model:
  `absolute` when clip and model belong together, `ratio:<h>` when the clip was authored on
  a rig whose standing hips height is `h` (take the 90th percentile of `dataset info`'s
  "hips y" from an `absolute` build to estimate it), `normalized` for Godot's motion-scale
  convention.
* `dataset info` prints limb lengths, ground-contact percentiles (the lowest foot should sit
  near the rest foot height for standing clips) and the rest deviation from the humanoid
  profile, which must be ~0°.

### 2c. Rig with other bone names (Mixamo, MMD/VRM `J_Bip_*`, BVH-style, your own)

```sh
scripts/ikh dataset build --model dancer.glb --anim dancer.glb --bone-map vrm --frames 40 --out out/datasets/dance.json
scripts/ikh dataset build --model perfume.glb --anim perfume.glb --bone-map bvh_perfume --frames 30 --out out/datasets/perfume.json
scripts/ikh dataset build --model mixamo.fbx --anim mixamo.fbx --bone-map mixamo --frames 30 --out out/datasets/mixamo.json
```

For any other rig write a JSON file mapping profile bones to your bone names and pass its
path to `--bone-map`:

```json
{"Hips": "pelvis", "Spine": "spine_01", "Chest": "spine_02", "Neck": "neck_01", "Head": "head",
 "LeftShoulder": "clavicle_l", "LeftUpperArm": "upperarm_l", "LeftLowerArm": "lowerarm_l", "LeftHand": "hand_l",
 "LeftUpperLeg": "thigh_l", "LeftLowerLeg": "calf_l", "LeftFoot": "foot_l", "LeftToes": "ball_l", "...": "..."}
```

(Keys: `python -c "from ikharness.retarget import PROFILE_BONES; print(PROFILE_BONES)"`.)
Behind the scenes a temporary Godot project with a `BoneMap` and a `.import` file is run
through `godot --import`, which renames the bones, forces a T-pose in the profile's axes and
rewrites the clip's tracks; `--keep-project DIR` keeps it for inspection. FBX files are read
by Godot's built-in ufbx importer; make sure they are real files, not git-LFS pointers.

### 2d. Add it to a suite

Copy `suites/default.json`, add your dataset (a `build` recipe lets `--build` regenerate it)
and the tracker sets you care about (`3pt`, `4pt`, `6pt`, `7pt`, `8pt`, `10pt`, `11pt`, or a
comma-separated role list), then:

```sh
scripts/ikh suite --suite suites/mine.json --ik builtin --build
```

## 3. Evaluate an IK implementation (Godot pipeline)

```sh
scripts/ikh eval --dataset out/datasets/mine.json --tracker-set 6pt --ik renik      # per-bone table
scripts/ikh eval --dataset out/datasets/mine.json --tracker-set 6pt --ik builtin
scripts/ikh eval --dataset out/datasets/mine.json --tracker-set 6pt --ik none       # rest pose baseline
scripts/ikh negative --dataset out/datasets/mine.json --ik builtin                  # must print "all ladders degrade monotonically"
```

What happens: virtual trackers are placed on the reference skeleton (head at the eyes,
hands, hips, feet, ... see `python/ikharness/trackers.py`), written to
`out/results/<dataset>_<ik>_<set>.trackers.json`, the Godot harness rebuilds the skeleton,
undoes the tracker offsets into bone targets (a T-pose calibration with trackers on the
bones), runs the IK for `--settle` frames per pose, and writes
`<...>.result.json`; the scorer compares rest-relative rotations per bone and writes
`<...>.score.json`.

To evaluate **your own IK**: add an adapter class in `godot/harness/harness.gd` (two
methods: `setup(root, skeleton, test)` and `apply_frame(harness, frame)`; the `renik` and
`builtin` classes are the templates) and pass its name to `--ik`. For an IK that lives in
another engine or program, consume the `.trackers.json` file (format in
`python/ikharness/testfile.py`), write an `ikharness-result/1` JSON with the solved global
bone poses and your rig's rest, and score it with:

```sh
scripts/ikh score --dataset out/datasets/mine.json --result my_result.json
```

## 4. Read poses back through ShaderMotion (pixels)

Three readouts for the same run:

```sh
scripts/ikh eval --dataset out/datasets/mine.json --tracker-set 6pt --ik builtin --readout json
scripts/ikh eval ... --readout shadermotion       # harness writes CPU-encoded PNGs, Python decodes them
scripts/ikh eval ... --readout shadermotion-gpu   # the recorder mesh + shader, rendered with software OpenGL (Xvfb)
```

Each prints `readout comparison (weighted deg): through pixels … | direct JSON … | pixels vs
raw reference …`. The pixel readouts score against the reference passed through the same
format, so the format's floor (about 1.6–2.7° on our datasets, mostly wrist twist) cancels.

Standalone use, for a decoder or capture chain of your own:

```sh
scripts/ikh shadermotion encode --dataset out/datasets/mine.json --out-dir out/sm_frames        # reference → PNGs
scripts/ikh shadermotion decode --skeleton out/datasets/mine.json --images captured_frames/ --out decoded.json
scripts/ikh score --dataset out/datasets/mine.json --result decoded.json --through-shadermotion
```

Frames must be the plain ShaderMotion layout (80×45 squares, avatar in the left three slot
columns, any resolution; `--grid-w 6` for a one-avatar crop, `--layer 1` for a second
avatar). The spec and our findings are in `docs/shadermotion.md`.

## 5. Drive an OpenXR / OpenVR application (Monado)

```sh
scripts/ikh service &                                   # headless runtime; prints XR_RUNTIME_JSON to export for clients
scripts/ikh devices                                     # HMD, two Index controllers, eight trackers
scripts/ikh replay --dataset out/datasets/mine.json --tracker-set 6pt --dwell 1.0 --verify   # stream frames, verify via OpenXR
scripts/ikh probe                                       # print every pose as an OpenXR client sees it
```

Any OpenXR application started with that `XR_RUNTIME_JSON` sees the head and hands (Valve
Index profile) and the trackers through Monado's `XR_MNDX_xdev_space`; OpenVR applications
can run on Monado's OpenVR target, where the trackers appear as generic trackers. The driver
holds each pose until the next frame, so `--dwell` is your settle time. Details, the wire
protocol (for feeding it from another program) and limitations: `docs/monado-driver.md`.

## 6. Where things end up, and how to know they are right

| Path | Content |
|---|---|
| `out/datasets/*.json` | reference datasets (`ikh dataset info` to inspect) |
| `out/results/<dataset>_<ik>_<set>.{trackers,result,score}.json` | one evaluation |
| `out/results/<…>.shadermotion[-gpu]/frame_*.png` | ShaderMotion frames of that run |
| `out/suite/<suite>_<ik>.json` | the single number and its entries |
| `out/negative/` | perturbation ladder runs |

Checks that catch broken conventions: `ikh eval --ik echo` must score 0.00°; `ikh negative`
must degrade monotonically; `ikh dataset info` must report ~0° rest deviation;
`scripts/run_tests.sh` runs the test suite in parts (see `docs/resources.md` for the memory
rules on small or shared machines).

## Troubleshooting

* **`ikh status` shows no class cache** → `godot --headless --path godot/harness --import`.
* **Exporter says "animation bones not in model (ignored)"** → the clip names bones the model
  lacks (often `UpperChest`, `Root`); harmless unless it lists body bones you expected.
* **Feet float or sink** in `dataset info` → wrong `--hips-mode`; try `ratio:<standing hips height>`.
* **Rest deviation ≫ 0°** → the rig is not in the profile convention; use `--bone-map`.
* **Memory** → every engine launch is guarded (3 GB cap, `IKH_MAX_RSS_MB`); on a shared box run
  one thing at a time (`docs/resources.md`).
* **GPU readout fails to start** → needs `Xvfb` (or set `IKH_GPU_LAUNCHER` to a command prefix
  that provides a display).
