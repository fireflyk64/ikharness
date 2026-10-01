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
- [x] More datasets: the other two Perfume clips, the Mixamo hip-hop dance and the No Logic David
      krump dance (`suites/extended.json`: builtin 18.65°, RenIK 21.72°, rest 76.91°), with frame
      selection by pose diversity (`ikh dataset build --select diversity`, `ikh dataset select`).
      Not added: the VRM avatars of the demo repo carry no clips of their own, and the
      "Mixamo catwalk" file there is a text mesh. (`docs/datasets.md`)
- [x] Godot's built-in IK as a second harness adapter (`--ik builtin`: FABRIK3D spine,
      TwoBoneIK3D limbs with pole nodes, hips + end-effector modifiers). Suite (4 datasets): builtin
      16.18°, RenIK 22.10°, rest pose 69.74°. (`docs/godot-harness.md`)

- [x] Memory guard for every Godot / Monado launch, headroom checks, test suite in parts,
      after the shared 8 GB container ran out of memory. (`docs/resources.md`)

## Round of 2026-09-30 (owner: fix squished projection, automate OpenXR feed + screen readout, T-pose calibration)

- [x] Squished projection: square 1024² eyes with symmetric 100° FOV in the driver, and the null
      compositor patched to recommend the HMD's per-eye size instead of 320×240 (`monado/patches/0002`).
- [x] Controller inputs over the wire (`INPUT`: triggers, buttons, sticks) and several clients with
      `GET_STATE`, so both triggers can be pushed remotely and a demo can read tracker poses.
- [x] Guard knows about process slots (`IKH_MIN_FREE_PIDS`, `ikh status`), after the container hit
      its pids limit through unreaped zombies.
- [x] T-pose calibration: reference rig in T-pose, wait a second, push both triggers.
      `ikh calibrate` / `ikh replay --calibrate` (gesture through the driver), harness
      `--calibration tpose` (root + per-tracker offsets derived from the T-pose frame),
      `tracker_mount` perturbation absorbed by calibration. (`docs/calibration.md`)
- [x] Godot OpenXR demo on Monado with the ShaderMotion recorder on screen (`godot/harness/xr_demo.gd`),
      `ikh xr` automation (service + demo + calibration by triggers + replay), poses read off the
      screen (`ikh screenshot`, Xvfb framebuffer / `import` / `ffmpeg`). Walk 6pt builtin: 16.67°
      through OpenXR and the screen vs 16.89° in-process. Controllers also emulate Touch because
      Godot's default action map has no Index profile. (`docs/godot-openxr.md`)

- [x] Found by cross-checking readouts (2026-09-30): the exporter wrote `rest_local` relative to
      non-humanoid parents (Perfume limbs 9.5 cm off in the harness; suite builtin 16.18° → 15.25°,
      RenIK 22.10° → 21.54°); T-pose calibration and the head tracker rule disagreed on the view
      point of rigs without eye bones (9 cm, +9° at 11 points on Perfume). Both fixed, with tests.

## Next

- [x] RenIK adapter: pole direction from the tracked lower bone (arm offset corrected), chest
      target without position, `RenIKPlacement3D` with a floor for 3/4-point sets. Suite
      21.54° → 19.80°; mocap 11-point 21.87° → 13.03° with hands on target. Placement is mixed
      (better on mocap 3pt, worse on walk), `IKH_RENIK_PLACEMENT=0` disables it.
      (`docs/godot-harness.md`)
- [x] Godot as OpenXR client through Monado (goal 5): head, hands and triggers through
      OpenXRInterface, body trackers through the driver's state query. (`docs/godot-openxr.md`)
- [x] `XR_HTCX_vive_tracker_interaction` in Monado's OpenXR layer (`monado/patches/0003`,
      upstream marks it ALWAYS_DISABLED): the 17 tracker roles as subaction paths, roles taken
      from device names, `xrEnumerateViveTrackerPathsHTCX`. The Godot demo now reads body
      trackers through OpenXR; the driver side channel is the fallback (`--trackers`).
- [x] Godot as preprocessor (goal 5): `ikh dataset export-retargeted` writes a dataset as GLB,
      Godot scene and Animation resource on the standard skeleton; `--verify` re-imports the
      GLB and scores 0.0000°. (`docs/datasets.md`)
- [x] ShaderMotion reference codec in Python (goal 6, step 1): colors ↔ numbers, hips float
      scheme, frame layout, images; validated on a genuine Unity-encoded frame.
      (`docs/shadermotion.md`)
- [x] ShaderMotion pose layer: bone rotations ↔ swing-twist angles (godot-humanoid tables),
      whole frames ↔ slots with projection residuals. Round-trip floor 1.6° (walk) / 2.7°
      (mocap) weighted; genuine frame reconstructs to a plausible standing pose.
- [x] Score "through ShaderMotion": the harness writes ShaderMotion PNGs (CPU encoder in
      GDScript), `ikh eval --readout shadermotion` reads poses back from the pixels and scores
      against the round-tripped reference. GDScript and Python encoders agree to 0.013°.
- [!] Report / fix upstream `swing_twist_inv` in V-Sekai/godot-shader-motion. Cause found
      (quaternions with w < 0 give swing vectors longer than π, which per-axis wrapping turns
      into a pose 119° off on average), report and patch written in `docs/upstream/`.
      Filing it on the upstream repository is the owner's call.
- [x] `ikh shadermotion encode|decode` for images and folders (video: extract frames with ffmpeg first).
- [x] ShaderMotion *shader* in Godot (goal 6): skinned recorder mesh (normals + positions only,
      skinned tangents proved unusable) and fragment shader, rendered with software OpenGL under
      Xvfb; `ikh eval --readout shadermotion-gpu`. Matches the Python encoder within 0.026°.
- [x] GPU readout with RenIK and on the full suite: recorder normals moved onto the bone axes
      (stretched bones read up to 12.6° wrong before, 0.11° now); `ikh suite --readout
      json|shadermotion|shadermotion-gpu|xr --calibration rules|tpose`. Suite through rendered
      pixels 20.53° builtin / 25.56° RenIK, through the OpenXR chain 20.64° / 25.56°.
- [x] Replication: `scripts/setup.sh` (pinned Godot, Python lock, data repos at pinned commits,
      vendored MIT walk clips), `docs/getting-started.md`, `docs/STATUS.md`, `ikh score` and
      `ikh shadermotion encode|decode` for external tools.
- [x] Capture path (goal 7), part 1: frames grabbed from a *separate* application's screen
      (`python/ikharness/screen.py`), decoded and scored (`ikh xr`). (`docs/godot-openxr.md`)
- [x] Capture path, part 2: recorded video. `ikh video roundtrip` quantifies codec loss
      (H.264 CRF 23: 0.09° mean, 0.26° max; CRF 35: 0.2° / 0.6°; suite score unchanged to 0.02°),
      `ikh xr --video PRESET` records the display live with ffmpeg x11grab and scores the
      recording (identical score). (`docs/shadermotion.md`)
- [x] Reports: `ikh report` compares suite reports and score files (markdown + JSON, deltas
      against a baseline, largest per-bone changes). (`docs/cli.md`)

## Later

- [ ] Unity harness + test avatar with compatible rest rotations, ShaderMotion unlit shader,
      pixel readback (goal 8/9). Blocked on Unity being installed. (`docs/unity.md`)
- [ ] Closed-source apps (VRChat): Monado OpenVR runtime target + ShaderMotion capture.
- [ ] BVH and VMD importers in Python (only if the GLB / .tres conversions turn out
      insufficient).
- [x] SteamVR plugin: generic trackers forwarded with their roles, and the per-eye render target
      size fixed (upstream reported the whole side-by-side screen, a 2:1 image for a square
      frustum: the likely "squished" view). `monado/patches/0004`, tested in a mock vrserver
      (`monado/tests/steamvr_mock_host.cpp`, `tests/test_steamvr_plugin.py`).
- [!] Try the plugin inside real SteamVR (not installed here): direct-mode / compositor
      behaviour and VRChat's tracker calibration cannot be checked with the mock host.

## Done

- [x] Monado `ikharness` driver: HMD, Index controllers, N trackers over TCP; null compositor;
      end-to-end OpenXR tests. (`docs/monado-driver.md`)
- [x] Reference datasets from Godot: `export_poses.gd`, `ikharness.build_dataset`, hips scaling
      modes; mocap and idle/walk datasets. (`docs/datasets.md`)
- [x] Tracker placement (3 to 11 point sets) and per-bone angular scoring. (`docs/scoring.md`)
- [x] Godot harness with RenIK adapter and no-IK baseline; first numbers
      (RenIK 6pt 13.2°, 11pt 11.2°, rest pose 38.3° on the walk set). (`docs/godot-harness.md`)
- [x] Dataset replay into the Monado driver with OpenXR read-back verification.
