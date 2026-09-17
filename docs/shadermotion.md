# ShaderMotion: bone rotations as pixels

**Goal.** Read an avatar's pose out of a closed-source application by rendering it with a
shader that encodes each bone's rotation into screen pixels (ShaderMotion), capturing the
frames (screenshots or video), and decoding them back into bone rotations that the scorer
understands. Godot first, then Unity.

**State.** Codec and pose layers in Python (validated on a genuine Unity-encoded frame), a
CPU encoder and a **GPU recorder mesh + shader** in Godot, all agreeing to a few hundredths
of a degree. Next: capture from a separate application window / video, then Unity.

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

### What exists: `python/ikharness/shadermotion/humanoid.py` (pose layer)

`local_rotation = preQ · swing_twist(sign · angles) · postQ⁻¹`, with the tables dumped from
godot-humanoid's `human_trait.gd` (`godot/tools/dump_human_trait.gd` → `human_trait.json`,
Apache-2.0, see `NOTICE.md`). `frame_to_slots` turns a dataset frame into slot values (hips:
world position in meters and rotation columns, mirrored in X for Unity's handedness);
`slots_to_frame` rebuilds a frame by forward kinematics on a given skeleton.

Things worth knowing:

* **Zero is not the T-pose.** Angles are relative to Mecanim's neutral "motorcycle" pose: a
  straight knee or elbow reads **+80°**, a T-pose upper leg +30°, the spine 0°.
* **Not every rotation fits.** Hinges have no swing about Y, hands no twist, fingers two
  axes. Encoding projects onto what the joint can express, reports the residual and hands it
  to the children (Mecanim's twist distribution).
* **Measurement floor.** Reference → slots → 8-bit image → slots → pose costs
  **1.6° weighted on the walk set and 2.7° on the mocap set** (end effectors 0.9 / 2.2 cm),
  almost all of it in the hands (7–12°: wrist twist has nowhere to go) and forearms. IK
  implementations differ by 13–22°, so the readout is usable. When scoring an application
  through ShaderMotion, pass the *reference* through the same round trip first so the floor
  cancels.
* **Tables are avatar specific.** They were exported from one avatar; a perfectly straight
  profile leg sits 1.5° off its knee hinge plane. A Unity export for our own test avatar
  would remove that.
* **Genuine frame.** The Unity-encoded test frame reconstructs to a standing VR user, legs
  straight and symmetric, left limbs on the left, elbows behind the torso and both hands
  raised in front of the chest (elbows flexed ~140°). That confirms signs and handedness for
  limbs; the hips mirror convention still deserves a check against a frame of a *known* pose.

### What exists: Godot CPU encoder and pixel readout

`godot/harness/shadermotion_encoder.gd` encodes every solved pose to a ShaderMotion PNG
(`--shadermotion-dir`, no renderer needed; godot-humanoid's tables vendored under
`godot/harness/addons/humanoid`). `ikh eval ... --readout shadermotion` then reads the poses
back **from the pixels** in Python and scores them against the reference passed through the
same format, printing the comparison with the direct JSON readout:

```
readout comparison (weighted deg): through pixels 14.70 | direct JSON 11.74 | pixels vs raw reference 15.94
```

* The GDScript encoder and the Python encoder agree to **0.013°** on the same solved poses
  (`tests/test_godot_harness.py::test_shadermotion_pixel_readout_matches_python_encoder`).
* Upstream's GDScript `swing_twist_inv` (`transform_util.gd`) is unreliable: it divides by
  near-zero twist terms and patches quaternion signs ad hoc, which sent shoulders, upper
  arms and a foot 90–160° off in this pipeline. The encoder here carries its own inverse (w ≥ 0
  representative, direct 2×2 solve). Worth fixing upstream.
* The remaining gap to the JSON readout is format loss, and it depends on the IK: the
  `builtin` adapter leaves forearm and shin roll unsolved and then forces hand and foot
  orientation, so wrists carry 27–40° of twist that a Mecanim hand joint cannot express.
  Through pixels that error lands on the hand; through JSON it lands on the forearm.

### What exists: the GPU recorder (`godot/harness/shadermotion_gpu.gd`)

This is ShaderMotion the way it has to work inside a closed application: nothing but a
skinned mesh and a shader on the avatar. `ikh eval ... --readout shadermotion-gpu` renders
it with software OpenGL (llvmpipe) on a private Xvfb display, saves the SubViewport and
scores from those pixels.

![a rendered ShaderMotion frame](img/shadermotion_gpu_frame.png)

* **One triangle per bone**, three vertices, using only what skinning reliably transforms:
  A (bone): normal +X, position = joint. C (bone): normal +Y. B (parent): normal +X, position =
  joint + Y. After skinning that yields `R_d(bone)·x`, `R_d(bone)·y`, `R_d(parent)·x`, and
  `R_d(parent)·y` as the difference of two skinned positions, where `R_d = pose · rest⁻¹` is
  exactly the rest-relative rotation the scorer uses.
* **No geometry shader needed.** Each vertex multiplies its data by a role flag; the
  fragment shader divides by (or normalizes away) the interpolated barycentric weight. The
  triangle is oversized (corners at −1 and 4 in slot-rectangle units) so every weight stays
  ≥ 1/5 inside the rectangle; fragments outside are discarded.
* **Per-bone constants** ride in `CUSTOM0..3`: `L = preQ⁻¹·G_parent⁻¹`, `R = G_bone·postQ`,
  signs with locked axes zeroed, first slot and channel map. The fragment shader computes
  `swing = L · (R_d(parent)⁻¹ · R_d(bone)) · R`, the swing-twist inverse, and the Gray-curve
  color of the square it is drawing. Hips: world position (wide float hi/lo) and the posed
  basis columns, mirrored in X.
* **Accuracy**: against the Python encoder on the same solved poses, all 100 angle slots
  agree within **0.026°** (mean 0.002°), hips position within 0.1 mm, avatar scale exact
  (`tests/test_godot_gpu.py`).
* **Cost**: about 330 MB and a few seconds per run under llvmpipe (`docs/resources.md`).

Findings worth keeping (`godot/gpu_probe/` has the probes):

* **Skinned tangents are unusable** in the Compatibility renderer: a supplied tangent of +Y
  reaches the vertex shader as roughly (0.55, 0, −0.86) while normals and positions are
  exact. The first recorder design used normal + tangent and produced rotations with Y and Z
  confused; the redesign above avoids tangents entirely.
* The SubViewport image has clip-space +Y at the bottom, and colors written to `ALBEDO`
  come back unchanged except the darkest ~7 levels (up to 7/255 low), which costs at most
  0.03° on the one interpolated digit.
* A shader cannot pass one bone's unrepresentable twist to its children, so the GPU path
  scores against a reference round-tripped without leftover propagation.
* Non-uniform bone scale (RenIK's stretch) skews skinned normals slightly; the GPU path has
  only been validated with the `builtin` adapter.

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
2. ~~**Godot encoder.**~~ Done, CPU (GDScript) and GPU (recorder mesh + shader), see above.
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
