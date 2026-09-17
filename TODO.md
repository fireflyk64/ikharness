# TODO

Living task list for ikharness. Update it in the same change that moves a task.
Markers: `[ ]` planned, `[~]` in progress, `[x]` done, `[!]` blocked or needs a decision.
Each subproject has a document under [`docs/`](docs/README.md) with how to run it, how to
test it, and how to see whether it is working (the feedback loop).

## Goals (project owner, 2026-09-15)

1. **Rest-rotation independence.** Scores must not depend on a rig's rest orientations.
   Focus on Godot first, then use Godot to drive OpenXR. Use Godot's import retargeter
   (BoneMap + SkeletonProfileHumanoid via the `.glb.import` / `.fbx.import` options) or the
   runtime `RetargetModifier3D` node to bring any rig onto the standard humanoid.
2. **One tool to drive everything.** Running many scripts is confusing; provide a README, helper
   scripts and a command line (or interactive) driver.
3. **One number.** Run through a lot of test data and produce a single final score for an IK
   implementation.
4. **Negative tests.** Options to deliberately disrupt parts of the system (arms in the wrong
   place, wrong rotations) and confirm the number goes down; use these to iterate on the
   evaluation itself.
5. **Godot as driver or preprocessor.** Either Godot drives OpenXR / other systems through the
   realtime retarget, or Godot pre-generates retargeted test data that is safe to use against
   OpenXR, Unity or other engines.
6. **ShaderMotion in Godot.** Find or write a Godot port of ShaderMotion so Godot (and Unity)
   can emit bone rotations as pixels.
7. **Capture.** Capture screen pixels per animation frame, or record a video and read it back
   into Godot. Fork https://github.com/V-Sekai/shader-motion-navy-lead-ostrich or
   https://github.com/V-Sekai/godot-shader-motion.
8. **Unity avatar + shader.** A test Unity avatar with compatible rest rotations, ShaderMotion as
   an unlit Unity shader outputting rotations as pixels, fed back in. Do the end-to-end flow in
   Godot first.
9. **Port to Unity** once Unity is available (it is not installed yet).

## Now

- [x] Write TODO.md and the per-subproject docs with test / feedback-loop notes. (`docs/`)
- [x] Score on rest-relative rotations (goal 1): compare `pose × rest⁻¹` per bone, with each side
      supplying its own rest, so rigs with different bone axes but the same T-pose compare
      equal. Harness result files carry the harness skeleton's rest. (`docs/scoring.md`)
- [x] `ikh` command line driver (goal 2): `ikh dataset build`, `ikh eval`, `ikh suite`, `ikh negative`,
      `ikh replay`, `ikh probe`, `ikh service`, `ikh status`. (`docs/cli.md`)
- [x] Suite + single number (goal 3): `suites/default.json` lists datasets × tracker sets with
      weights; `ikh suite` prints one final score per implementation. First numbers:
      RenIK 17.57°, rest pose 48.76°. (`docs/scoring.md`)
- [x] Negative tests (goal 4): perturbations on the tracker inputs (`--perturb`) and on solved
      results; `ikh negative` runs the ladder and checks monotonic degradation;
      `tests/test_negative.py` guards the metric. (`docs/scoring.md`)
- [x] Godot import retargeter in the dataset exporter (goal 1/5): `--bone-map` presets
      (`vrm`, `bvh_perfume`, `mixamo`) or JSON; Perfume and MMD datasets added to the suite
      with 0.0° rest deviation from the profile. (`docs/datasets.md`)
- [ ] More datasets from the retargeting demo repo (Mixamo catwalk, hip-hop and No Logic David
      dance GLBs, VRM avatars) and the other two Perfume clips; frame selection by pose diversity.
- [x] Godot's built-in IK as a second harness adapter (`--ik builtin`: FABRIK3D spine,
      TwoBoneIK3D limbs with pole nodes, hips + end-effector modifiers). Suite (4 datasets): builtin
      16.18°, RenIK 22.10°, rest pose 69.74°. (`docs/godot-harness.md`)

- [x] Memory guard for every Godot / Monado launch, headroom checks, test suite in parts,
      after the shared 8 GB container ran out of memory. (`docs/resources.md`)

## Next

- [ ] RenIK adapter: tune pole-target feeding for elbow/knee trackers (on the mocap set the
      11-point run leaves hands 3.8 cm off, 6-point 0.4 cm); enable
      `RenIKPlacement3D` for 3/4-point sets (needs a floor collider). (`docs/godot-harness.md`)
- [ ] Godot as OpenXR client through Monado (goal 5): Godot scene reading head/hands via
      OpenXRInterface, trackers via a Monado extension Godot understands. Godot has no
      `XR_MNDX_xdev_space` support, so this needs `XR_HTCX_vive_tracker_interaction` in Monado's
      OpenXR layer (marked ALWAYS_DISABLED upstream). (`docs/godot-openxr.md`)
- [ ] Godot as preprocessor (goal 5): `ikh dataset export-retargeted` writes retargeted
      animations (Godot `.res`, glTF) with the standard rest, for OpenXR/Unity consumers.
- [x] ShaderMotion reference codec in Python (goal 6, step 1): colors ↔ numbers, hips float
      scheme, frame layout, images; validated on a genuine Unity-encoded frame.
      (`docs/shadermotion.md`)
- [ ] ShaderMotion pose layer: bone rotations ↔ swing-twist angles in Unity's calibrated axes
      (neutral pose, pre/post rotations, limit signs), round trip on dataset frames.
- [ ] ShaderMotion in Godot (goal 6): encoder shader on the harness avatar + GDScript decoder
      (from `V-Sekai/godot-shader-motion`), checked against the Python codec. Needs a real
      renderer (OpenGL on the X display), so measure memory under the guard first.
- [ ] Capture path (goal 7): per-frame screen capture and video recording (MovieWriter / PNG
      sequence) plus decode back to bone rotations in Godot; round-trip test against the
      dataset. (`docs/shadermotion.md`)
- [ ] Reports: `ikh report` comparing several runs (markdown table + JSON), per-bone deltas.

## Later

- [ ] Unity harness + test avatar with compatible rest rotations, ShaderMotion unlit shader,
      pixel readback (goal 8/9). Blocked on Unity being installed. (`docs/unity.md`)
- [ ] Closed-source apps (VRChat): Monado OpenVR runtime target + ShaderMotion capture.
- [ ] BVH and VMD importers in Python (only if the GLB / .tres conversions turn out
      insufficient).
- [ ] SteamVR plugin: forward generic trackers (upstream `steamvr-monado` only does HMD + hands).
- [ ] Frame selection by pose diversity instead of uniform sampling.

## Done

- [x] Monado `ikharness` driver: HMD, Index controllers, N trackers over TCP; null compositor;
      end-to-end OpenXR tests. (`docs/monado-driver.md`)
- [x] Reference datasets from Godot: `export_poses.gd`, `ikharness.build_dataset`, hips scaling
      modes; mocap and idle/walk datasets. (`docs/datasets.md`)
- [x] Tracker placement (3 to 11 point sets) and per-bone angular scoring. (`docs/scoring.md`)
- [x] Godot harness with RenIK adapter and no-IK baseline; first numbers
      (RenIK 6pt 13.2°, 11pt 11.2°, rest pose 38.3° on the walk set). (`docs/godot-harness.md`)
- [x] Dataset replay into the Monado driver with OpenXR read-back verification.
