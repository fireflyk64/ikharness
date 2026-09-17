Parts of this package are ported from Apache-2.0 licensed work and keep that license:

* `humanoid.py` (swing-twist math, rotation <-> muscle triplet conversion) and
  `human_trait.json` (tables dumped by `godot/tools/dump_human_trait.gd`) come from
  godot-humanoid / V-Sekai godot-shader-motion (`humanoid/transform_util.gd`,
  `human_trait.gd`): Copyright 2022-2023 lox9973, Copyright 2023-present Lyuma and
  contributors. License: https://www.apache.org/licenses/LICENSE-2.0

`codec.py` is an independent implementation of the ShaderMotion format following
`shader_motion_specification` (MIT, lox9973) and the reference decoder's published behaviour.
