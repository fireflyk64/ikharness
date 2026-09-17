# Godot harness

**Goal.** Run an IK implementation inside Godot on the standard skeleton, driven by the
virtual trackers, and dump the solved bone poses. Adapters make implementations
interchangeable.

**State.** Working with RenIK (V-Sekai GDScript port, vendored under
`godot/harness/addons/renik`), Godot's built-in modifiers (`builtin`) and a no-IK baseline. Engine details: [`../godot/README.md`](../godot/README.md).

## Run

```sh
godot --headless --path godot/harness --import       # once per clone (script class cache)
ikh eval --dataset out/datasets/vsk_walk.json --tracker-set 6pt --ik renik
ikh eval ... --ik none                               # baseline
ikh eval ... --perturb hands_offset:0.1              # negative test on the inputs
```

Under the hood: `ikharness.testfile.build_test_file` → `godot/harness/harness.gd` →
`out/results/<dataset>_<ik>_<set>.result.json` → `ikharness.scoring`.

## How the harness works

* Rebuilds `Skeleton3D` from the test file's skeleton block (adds a `Root` above `Hips`).
* Converts every tracker back into a bone target by undoing the tracker rule offset;
  roles outside the tracker set get hidden target nodes so the solver must infer them.
* Holds each frame for `--settle` processed frames (default 8), then captures the solved
  global poses **inside `skeleton_updated`**, because Godot restores pre-modifier poses
  after each update.
* Writes the result with the rig's rest orientations, so the scorer can be rest-relative.
* With `--shadermotion-dir` it also writes each solved pose as a ShaderMotion PNG
  (`ikh eval --readout shadermotion` scores from those pixels, see [shadermotion.md](shadermotion.md)).

## Adapters

| `--ik` | Notes |
|---|---|
| `renik` | spine (head/hip/chest targets), trig limbs (hands, feet), elbow/knee trackers as pole targets |
| `none` | rest pose |
| `echo` | Python-side: returns the reference itself (scorer sanity check, no Godot) |
| `builtin` | Godot built-ins: `FABRIK3D` Spine→Head chain, `TwoBoneIK3D` per limb with pole nodes (elbow/knee trackers, else a heuristic point behind the elbow / in front of the knee), custom modifiers for hips placement and end-effector orientation |

An adapter is a class in `harness.gd` with `implementation_name()`, `setup(root, skeleton,
test)` and `apply_frame(harness, frame)`.

## Feedback loop

* `pytest tests/test_godot_harness.py` (mini dataset, ~10 s): hips/head/feet within 0.1°,
  hands within 3 cm, RenIK better than the baseline.
* `IKH_DEBUG=1 ikh eval ...` prints target vs achieved positions for frame 0.
* `ikh negative --ik renik` must pass after adapter changes.

## Known issues

* Elbow trackers fed as RenIK pole targets improve knees but worsen upper arms; RenIK uses
  the pole node's orientation, not position. Needs a proper conversion.
* 3/4-point sets leave legs at rest: `RenIKPlacement3D` (raycast foot placement) is not
  wired up and needs a floor collider in the harness scene.
* Hips and head are exact by construction for RenIK (it adopts those targets).
