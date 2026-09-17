# ShaderMotion: bone rotations as pixels

**Goal.** Read an avatar's pose out of a closed-source application by rendering it with a
shader that encodes each bone's rotation into screen pixels (ShaderMotion), capturing the
frames (screenshots or video), and decoding them back into bone rotations that the scorer
understands. Godot first, then Unity.

**State.** Codec layer done and validated against a genuine frame; pose layer, Godot shader
and capture are next.

### What exists: `python/ikharness/shadermotion/codec.py`

A pure-numpy reference of the format, from `shader_motion_specification` and lox9973's
reference decoder, meant as the oracle for the Godot and Unity shaders:

* **Number ↔ two colors.** A value in [-1, 1] rides a continuous base-3 Gray curve through
  the 6-cube (729 levels, linear between levels), written as G,R,B,G,R,B. Exact round trip;
  after 8-bit color quantization the error is below 0.01° for angles.
* **Wide float in two slots** (hips position / 2): `hi` and `lo`, where `lo` reflects at the
  ends of each block so the pair is continuous. Inside ±1 (±2 m) `hi` is exactly 0, so sloppy
  decoders may ignore it. Neither upstream repo ships an encoder for this; ours is derived
  from the reference decoder and round-trips to 1e-9.
* **Layout.** 80 × 45 squares, 40 × 45 slots indexed column-major; one avatar uses three slot
  columns (slots 0..129): hips (position hi/lo, rotation matrix y and z columns scaled so
  `|y|/|z|` is the avatar scale), then swing-twist XYZ angles / 180° per bone, fingers YZ+Z+Z,
  eyes YZ, toes Z. Further avatars are "layers", odd layers mirrored from the right edge.
* **Images.** `encode_frame` / `decode_frame` on numpy arrays at any resolution; squares are
  sampled over their central half to tolerate scaling and compression bleed; cropped strips
  decode with `grid_w`.

`tests/test_shadermotion_codec.py` (11 tests) includes decoding
`tests/data/shadermotion/upstream_frame.png`, a frame from the original Unity encoder: hips
at (0.00, 1.03, 0.21) m, avatar scale 0.894, rotation columns orthogonal to 0.0005, small
spine angles, bent left arm and knee. That is independent evidence the decoder is right.

### Candidates found upstream

| Repository | What | Notes |
|---|---|---|
| `V-Sekai/godot-shader-motion` (fork of `vr-voyage/shadermeowmeow`) | GDScript ShaderMotion *decoder* pieces: block/slot analyzers, swing-twist tests, mecanim bone mapping, frame-to-sprite tools | Godot project; `shader_motion/` and `vsk_utility/` dirs |
| `V-Sekai/shader-motion-navy-lead-ostrich` | JavaScript web player: `MotionDecoder.js`, `ScreenCapture.js`, `AnimRecorder.js`, `shader_motion_specification/`, shaders, glTF loader | best written description of the format |
| `V-Sekai/shader-motion-video-to-anims` | Python: video → animations | 2022 |
| lox9973/ShaderMotion | the original Unity implementation (unlit shader + recorder) | not reachable through the GitHub API at the moment |

## Plan

1. ~~**Spec first.**~~ Done, see above.
1b. **Pose layer** (next): swing-twist angles live in Unity's *calibrated* bone axes relative
   to the Mecanim neutral pose (`bone_rotation.md` has the axis table; the Godot port's
   `core/humanoid/human_trait.gd` and `transform_util.gd` carry pre/post rotations and limit
   signs). Implement `rotations → angles` and back in Python, test the round trip on dataset
   frames, and express the result as rest-relative deltas for the scorer.
2. **Godot encoder.** A Godot shader (or CPU GDScript reference encoder) that, given the
   harness skeleton, writes the ShaderMotion tile image into a `SubViewport`. Verify with
   the vendored decoder: encode → decode must reproduce the input rotations. This is the
   round trip and it needs no capture yet.
3. **Godot decoder** from the vendored GDScript, hardened to run headless on an `Image`.
4. **Capture.** Per-frame `SubViewport.get_texture().get_image()` (in-process), a PNG
   sequence written by the app, or a recorded video (`MovieWriter` for Godot apps;
   OBS / ffmpeg for others) decoded with `VideoStreamTheora` or ffmpeg → PNG.
5. **Mapping.** ShaderMotion bones are Unity Mecanim bones; map to the humanoid profile and
   handle the muscle-space conventions (see `V-Sekai-game/addons/humanoid` for a Godot port
   of the Mecanim muscle encoding).
6. **Scoring.** Decoded rotations are rest-relative deltas already; feed them to the scorer
   as a result file with the avatar's rest.

## Feedback loop

* Encoder → decoder round trip on synthetic rotations: max error < 1° (quantisation).
* Encode a dataset frame, decode, score against the dataset: < 1°.
* Capture path: render → PNG → decode gives the same as in-process decode.
* Video path: encode → MovieWriter → decode; quantify compression error, choose codec /
  bitrate accordingly.
