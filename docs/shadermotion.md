# ShaderMotion: bone rotations as pixels

**Goal.** Read an avatar's pose out of a closed-source application by rendering it with a
shader that encodes each bone's rotation into screen pixels (ShaderMotion), capturing the
frames (screenshots or video), and decoding them back into bone rotations that the scorer
understands. Godot first, then Unity.

**State.** Planned. Candidates found:

| Repository | What | Notes |
|---|---|---|
| `V-Sekai/godot-shader-motion` (fork of `vr-voyage/shadermeowmeow`) | GDScript ShaderMotion *decoder* pieces: block/slot analyzers, swing-twist tests, mecanim bone mapping, frame-to-sprite tools | Godot project; `shader_motion/` and `vsk_utility/` dirs |
| `V-Sekai/shader-motion-navy-lead-ostrich` | JavaScript web player: `MotionDecoder.js`, `ScreenCapture.js`, `AnimRecorder.js`, `shader_motion_specification/`, shaders, glTF loader | best written description of the format |
| `V-Sekai/shader-motion-video-to-anims` | Python: video → animations | 2022 |
| lox9973/ShaderMotion | the original Unity implementation (unlit shader + recorder) | not reachable through the GitHub API at the moment |

## Plan

1. **Spec first.** Extract the encoding from `shader_motion_specification/` and
   `MotionDecoder.js`: layout of the tiles, how a rotation is split (swing-twist), value
   ranges, the bone list (Unity Mecanim order) and the hips position encoding.
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
