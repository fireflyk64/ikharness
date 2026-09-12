# Godot tools and harness

Godot 4.7 (headless) is used for two things: importing animations into
reference pose datasets, and running IK implementations under test.

```
godot/tools/     headless scripts: dump_humanoid_profile.gd, inspect_model.gd, export_poses.gd
godot/harness/   the IK harness project (harness.gd + adapters) with RenIK vendored under addons/
```

Install Godot 4.7.x (`Godot_v4.7.2-stable_linux.x86_64`) and either put it on
`PATH` as `godot` or set `GODOT=/path/to/godot`. Run
`godot --headless --path godot/harness --import` once after cloning so the
project's script class cache exists (the Python runner does not do this).

## Conventions

Everything uses Godot's `SkeletonProfileHumanoid` convention: +Y up, the
character faces **+Z**, bone local +Y runs along the bone, and the rest pose
is a T-pose. `godot/tools/humanoid_profile.json` is the dump of that profile.
OpenXR stage space faces -Z, so replaying to the Monado driver applies a
180 degree yaw (see `python/ikharness/trackers.py`).

## Reference pose export (`export_poses.gd`)

Samples N frames of a clip on a humanoid model and writes the *global* pose of
every humanoid bone (`ikharness-poses/1`, see `python/ikharness/dataset.py`).
Models load at runtime through `GLTFDocument`/`FBXDocument` (no import step
needed). Clips can be Godot `.tres`/`.res` animations or clips embedded in a
GLB.

Hips position keys need care when a clip was authored for a rig of another
size. `--hips-mode ratio:<h>` scales them by `model_hips_height / h`, where
`h` is the standing hips height of the source rig (a good estimate is the 90th
percentile of the clip's hips height). `absolute` keeps the keys, `normalized`
multiplies by the model's hips height (Godot's motion-scale convention).

The Python wrapper merges several clips into one dataset:

```sh
python -m ikharness.build_dataset --model avatar.glb --anim idle.tres --anim walk.tres \
    --frames 15 --hips-mode ratio:0.995 --out out/datasets/walk.json
```

Skeleton3D only refreshes cached global poses during a processing frame, which
tool scripts do not have, so the exporter evaluates forward kinematics itself.

## Harness (`harness.gd`)

```sh
godot --headless --path godot/harness -s harness.gd -- --trackers test.json --out result.json --ik renik --settle 8
```

* Builds a `Skeleton3D` from the test file's skeleton block (adds a `Root`
  bone above `Hips` when missing).
* Turns each tracker pose back into a bone target by undoing the tracker
  rule's offset, which is exactly what a T-pose calibration with trackers
  placed on the bones would yield. Roles missing from the tracker set have
  their target nodes hidden, so the solver has to infer them.
* Holds each pose for `--settle` processed frames, then records the solved
  global poses. Godot applies `SkeletonModifier3D` nodes during the
  skeleton's own update and restores the unmodified pose afterwards, so the
  result is captured inside the `skeleton_updated` signal.
* Writes `ikharness-result/1` (see `python/ikharness/testfile.py`).

### Adapters

| `--ik` | What |
|---|---|
| `renik` | RenIK GDScript port (vendored from V-Sekai, MIT): spine solver with head/hip/chest targets, trig limb solvers for arms and legs, elbow/knee trackers fed as pole targets |
| `none` | no solver, the rest pose (baseline) |

Adding an implementation means adding a class in `harness.gd` (or a script it
preloads) with `setup(root, skeleton, test)` and `apply_frame(harness, frame)`.

### Known limitations

* Without foot trackers RenIK's automatic foot placement (`RenIKPlacement3D`,
  raycast based) is not enabled yet, so legs stay in rest for 3-point and
  4-point sets.
* RenIK reads a pole target's *orientation*; feeding elbow trackers straight
  in helps the knees a lot but currently makes the upper arms worse. The
  adapter's pole handling needs tuning before elbow numbers are meaningful.
* Hips and head score zero by construction, because the solver adopts those
  targets directly; they still matter for other implementations.

## Running an evaluation

```sh
python -m ikharness.run_godot --dataset out/datasets/vsk_walk.json --tracker-set 6pt --ik renik
```

Writes `out/results/<dataset>_<ik>_<set>.{trackers,result,score}.json` and
prints the per-bone table.
