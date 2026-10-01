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
| `renik` | spine (head/hip/chest targets), trig limbs (hands, feet), elbow/knee trackers as pole directions, `RenIKPlacement3D` for untracked feet and hips |
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

## What the RenIK adapter had to get right (2026-09-30)

RenIK is used with its own defaults (`assign_arm_defaults` / `assign_leg_defaults`); the
adapter only translates between the harness's conventions and RenIK's. Three translations
turned out to matter, each found from per-bone numbers (`ikh report` on two score files):

* **Pole targets are directions.** A limb bends in the plane through its root, its target
  and the point 1000 m along the pole node's +Z, turned about Y by `lower_twist_offset`.
  Measured on four datasets, the hinge axis of every limb in the humanoid profile is the
  lower bone's local X, so the in-plane direction is the lower bone's −Z. RenIK's leg offset
  (π) gives exactly that when the pole node is the knee tracker's bone transform; its arm
  offset (−π/2) gives the hinge axis itself, which leaves the bend plane undefined. The
  adapter turns the arm pole node by the difference. Upper arm error on the walk set at
  11 points: 46° → 16°. (Putting the pole point *at* the tracked joint is exact for bent
  limbs but flips straight limbs by 180°, where the joint lies on the root-target line.)
* **The chest target must not carry position.** RenIK's spine solver adds
  `chest target − chest bone pose before solving` (clamped to 0.3 m) to the head target. That
  works when the skeleton node follows the player; in the harness the skeleton stands still
  while the pose moves, and the head target was dragged away: shoulders 16 cm off, hands
  3.8 cm short on the mocap set. The adapter passes the chest tracker's orientation at the
  chest bone's own position. Mocap 11 points: 21.87° → 13.03°; Perfume 11 points:
  24.19° → 15.24°.
* **Foot placement for 3 and 4 points.** Without foot trackers the adapter adds a floor
  collider and a `RenIKPlacement3D` (feet by raycast under the head, hips too without a
  waist tracker). The harness shows unrelated poses, so each new pose is placed at once
  (`foot_place(..., instant)` on a physics tick, zero velocity) instead of walked to; a live
  application (`xr_demo.gd`) lets the gait run. `crouch_ratio` is set from the avatar's
  standing proportions (RenIK's 0.4 put the hips 15 cm too high and 18 cm back). The effect
  on the score is mixed, which is a finding about the solver, not a harness defect:

  | weighted° | 3pt placed | 3pt legs at rest | 4pt placed | 4pt legs at rest |
  |---|---|---|---|---|
  | walk | 28.5 | 19.9 | 23.0 | 19.1 |
  | mocap | 37.8 | 46.7 | 32.9 | 30.6 |

  Placement ignores the waist tracker and puts both feet close together under the head;
  for walking data legs hanging from the tracked hips are nearer the truth.
  `IKH_RENIK_PLACEMENT=0` switches it off.

What remains is RenIK's own behaviour: the forearm takes two thirds of the hand's twist
(`lower_limb_twist`), while most animation data keeps the twist in the wrist, a constant
50° on the lower arm bones of the walk set.

## Known issues

* Hips and head are exact by construction for RenIK (it adopts those targets).
* RenIK's placement has more to tune (stance width, floor offset); the adapter only sets
  the crouch ratio.
