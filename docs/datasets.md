# Reference pose datasets

**Goal.** Turn known-good animations from many sources into one file format on one
standard skeleton, with the limb lengths recorded, so every harness solves exactly the same
problem.

**State.** Working. Models with Godot humanoid bone names export directly; any other rig
goes through Godot's import retargeter with a bone map (`--bone-map vrm|bvh_perfume|mixamo|
file.json`), which renames bones, forces a profile-conformant T-pose (silhouette fix) and
rewrites the clip tracks. Four datasets feed the default suite.

## Format

`ikharness-poses/1` (see `python/ikharness/dataset.py`):

* `skeleton`: humanoid bones with `rest_local` and `rest_global` T-pose transforms, hips /
  head / eye heights, source model name. Limb lengths derive from the rest joint positions.
* `frames[]`: for each sampled frame the global transform of every humanoid bone, plus the
  source clip id and time.
* `sources[]`: clip path, clip name, length, hips scaling used.

## Run

```sh
ikh dataset build --model avatar.glb --anim idle.tres --anim walk.tres --frames 15 \
    --hips-mode ratio:0.995 --out out/datasets/walk.json
ikh dataset info out/datasets/walk.json          # bones, limb lengths, ground contact stats
```

`--hips-mode` handles hips position keys authored for a rig of another size:
`ratio:<h>` scales by `model_hips_height / h` (use the clip's 90th-percentile hips height for
`h`), `absolute` keeps them, `normalized` multiplies by the model hips height.

## Feedback loop

* `ikh dataset info` prints ground-contact percentiles: the lowest foot should sit near the
  rest foot height (0.085 m on the test avatar) for standing clips. Feet floating by 10 cm
  means the hips scale is wrong.
* `pytest tests/test_dataset_pipeline.py` covers load/save/merge, rest deltas, tracker
  placement and the test-file round trip on `tests/data/mini_walk.json`.
* The self-score of a dataset against itself must be 0 (`ikh eval --ik echo` does this).

## Retargeting (goal 1 / 5)

`ikharness.retarget` drives **Godot's own import retargeter**: it writes a throw-away
project containing the model, a `BoneMap` resource (`bone_map.tres`, generated from the
preset or JSON map) and a `.import` file whose `_subresources/nodes/"PATH:<skeleton>"` block
sets `retarget/bone_map`, the bone renamer (`GeneralSkeleton`, unique node) and the rest
fixer (`retarget_method = 1` "Overwrite Axis", `fix_silhouette/enable`, absolute position
tracks). `godot --headless --import` produces the retargeted scene, and the exporter loads
it as `res://<model>` inside that project.

```sh
ikh dataset build --model ANIM_aachan.glb --anim ANIM_aachan.glb --bone-map bvh_perfume --frames 30 --out out/datasets/perfume_aachan.json
ikh dataset build --model melt.glb --anim "melt.glb:MMD Animation melt" --bone-map vrm --frames 40 --out out/datasets/mmd_melt.json
ikh dataset info out/datasets/mmd_melt.json     # prints "rest vs humanoid profile: max 0.00 deg"
```

Adding a rig: write `{"Hips": "<source bone>", "LeftUpperArm": "...", ...}` as JSON (profile
bone names as keys, see `retarget.PROFILE_BONES`) or add a preset function in
`retarget.py`. Clips embedded in the model are retargeted with it; name a clip with
`model.glb:<clip name>` when the file holds several.

The runtime alternative, `RetargetModifier3D`, stays reserved for driving a differently
proportioned avatar live (Godot as an OpenXR driver).

**Checks.** `rest_conformance()` measures the angle between every body bone's rest and the
profile reference; it is 0.0° for all four datasets and `tests/test_retarget.py` asserts
< 0.5° after retargeting the Perfume rig. The V-Sekai avatar exported directly and through
the importer must match to floating point noise (to add as a test).

## Sources available locally

| Source | Bones | Clips | Status |
|---|---|---|---|
| `~/dev/animations/ANIM_test_assets` | humanoid names | 46 s mocap (`.tres` + in GLB) | used (`mocap08`) |
| `~/dev/V-Sekai-game/addons/vsk_game_framework/animations` | humanoid names | idle, walk × 8 (`.tres`) | used (`vsk_walk`) |
| `~/dev/animations/ANIM_perfume` | BVH names (`RightCollar`, `RightShoulder`, ...) | 3 × 94 s dance (GLB + BVH) | used (`perfume_aachan`, preset `bvh_perfume`) |
| `~/dev/animations/ANIM_mmd_vrm_sample` | VRM `J_Bip_*` | 261 s MMD dance (GLB + `.tres` + VMD) | used (`mmd_melt`, preset `vrm`) |
| `~/dev/animations/ANIM_female_doll_retargeting` | Mixamo gltf, VRMs, dance GLBs | retargeting demo | preset `mixamo` exists, datasets to add |
| `~/dev/animations/Basic Motions FREE` | `B-*` names | git-LFS pointers only | data missing |
