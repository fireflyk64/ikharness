# Reference pose datasets

**Goal.** Turn known-good animations from many sources into one file format on one
standard skeleton, with the limb lengths recorded, so every harness solves exactly the same
problem.

**State.** Working for models that already carry Godot humanoid bone names (V-Sekai test
avatar, godette) and clips in Godot `.tres` or GLB form. Rigs with other bone names
(Perfume BVH names, MMD/VRM `J_Bip_*` names) still need the retargeter step below.

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

## Retargeting plan (goal 1 / 5)

Two routes, both inside Godot so they match the engine's own conventions:

1. **Import retargeter** (preferred for datasets): generate a temporary project with the model
   and a `.import` file carrying `retarget/bone_map` (a `BoneMap` over
   `SkeletonProfileHumanoid`) and the `rest_fixer` options (`overwrite_axis`, `fix_silhouette`,
   `normalize_position_tracks`), run `godot --headless --import`, then load the imported
   `.scn` and export as today. This is what the `.import` files next to the Basic Motions FBX
   already do inside the V-Sekai project.
2. **`RetargetModifier3D`** (runtime): child of a source skeleton, transfers poses (or global
   poses) to child skeletons sharing profile bone names. Useful for driving a differently
   proportioned avatar live, e.g. Godot as an OpenXR driver.

Checks for either route: after retargeting, the T-pose of the target must equal the profile
(bone directions along +Y in bone space), and re-exporting a clip that already uses profile
names must reproduce the original dataset to floating point noise.

## Sources available locally

| Source | Bones | Clips | Status |
|---|---|---|---|
| `~/dev/animations/ANIM_test_assets` | humanoid names | 46 s mocap (`.tres` + in GLB) | used (`mocap08`) |
| `~/dev/V-Sekai-game/addons/vsk_game_framework/animations` | humanoid names | idle, walk × 8 (`.tres`) | used (`vsk_walk`) |
| `~/dev/animations/ANIM_perfume` | BVH names (`RightCollar`, `RightShoulder`, ...) | 3 × 94 s dance (GLB + BVH) | needs bone map |
| `~/dev/animations/ANIM_mmd_vrm_sample` | VRM `J_Bip_*` | 261 s MMD dance (GLB + `.tres` + VMD) | needs bone map |
| `~/dev/animations/ANIM_female_doll_retargeting` | mixed (Mixamo, VRM, ...) | retargeting demo | to inspect |
| `~/dev/animations/Basic Motions FREE` | `B-*` names | git-LFS pointers only | data missing |
