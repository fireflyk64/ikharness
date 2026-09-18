# Data sources

The default suite uses one avatar and four clip sources. `scripts/fetch_animations.sh`
clones the external ones at the pinned commits into `$IKH_DATA_DIR` (default
`~/dev/animations`).

| Name | Where | Pinned | License | Used for |
|---|---|---|---|---|
| V-Sekai test avatar + 46 s mocap | https://github.com/V-Sekai/ANIM_test_assets | d28a887 | see repo | `mocap08`, the default reference avatar (`vrm_1_vsekai_godot_engine_humanoid_08.glb`) |
| V-Sekai idle / walk clips | vendored in `data/vsk_animations/` (from V-Sekai/V-Sekai-game bcae971) | — | MIT | `vsk_walk` |
| Perfume dance (BVH-named rig) | https://github.com/V-Sekai-fire/ANIM_perfume | 79108f5 | see repo | `perfume_aachan` (bone map `bvh_perfume`) |
| MMD "melt" on a VRM avatar | https://github.com/V-Sekai/ANIM_mmd_vrm_sample | e0e870d | see repo | `mmd_melt` (bone map `vrm`) |
| Retargeting demo (Mixamo, VRMs, dances) | https://github.com/V-Sekai-fire/ANIM_female_doll_retargeting | — | see repo | optional, not in the suite (622 MB) |

The suite recipes in `suites/default.json` reference these paths; set `IKH_DATA_DIR` if you
clone elsewhere.
