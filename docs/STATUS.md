# Status report, 2026-09-17

Everything below is in the repository, committed and pushed, with tests. Usage is in
[getting-started.md](getting-started.md); the task ladder in [../TODO.md](../TODO.md).

## What the system does today

A reference animation is sampled into poses on a standard humanoid skeleton (Godot's
`SkeletonProfileHumanoid`, limb lengths recorded). Virtual trackers are placed on that
skeleton (3 to 11 point sets). An IK implementation solves the pose from the trackers; the
solved bone rotations are read back, either directly or through pixels, and scored against
the reference as rest-relative angular error per bone, reduced to one number per suite.

| Component | State |
|---|---|
| Dataset export (GLB / FBX / VRM-as-GLB, `.tres` or embedded clips) through Godot | working |
| Retargeting foreign rigs with Godot's importer (`vrm`, `bvh_perfume`, `mixamo`, JSON maps) | working, 0.0° rest deviation |
| Tracker placement, rest-relative scoring, suites (one number), negative ladders | working, tested |
| Godot harness: RenIK adapter, Godot built-in IK adapter, rest-pose baseline | working, tested |
| `ikh` command line, memory guard for a shared 8 GB container | working |
| ShaderMotion: Python reference codec + pose layer | working, validated on a genuine Unity frame |
| ShaderMotion: Godot CPU encoder and GPU recorder mesh + shader, scoring from rendered pixels | working, agree within 0.026° |
| Monado driver (HMD, Index controllers, N trackers over TCP), OpenXR read-back, dataset replay | working, tested |
| Capture from a separate application window / video | planned |
| Godot as an OpenXR client of the driver (needs HTCX in Monado) | planned |
| Unity avatar, shader and harness | blocked on Unity |

## Numbers (suite `default`: V-Sekai idle/walk + 46 s mocap on the V-Sekai avatar, Perfume dance, MMD dance; 6 and 11 point tracking; weighted degrees, lower is better)

| Implementation | final | quality | walk 6pt | walk 11pt | mocap 6pt | mocap 11pt | perfume 6pt | perfume 11pt | mmd 6pt | mmd 11pt |
|---|---|---|---|---|---|---|---|---|---|---|
| Godot built-in (TwoBoneIK3D + FABRIK3D) | **16.18** | 52.4 | 12.84 | 11.24 | 15.35 | 13.46 | 27.35 | 16.58 | 15.04 | 11.66 |
| RenIK (V-Sekai GDScript port) | **22.10** | 41.3 | 14.26 | 12.33 | 21.36 | 21.87 | 39.54 | 25.03 | 18.75 | 18.11 |
| none (rest pose) | **69.74** | 6.1 | 39.25 | 39.25 | 58.26 | 58.26 | 133.08 | 133.08 | 48.37 | 48.37 |

Readouts on the mini walk set (built-in IK, 6pt): direct JSON 11.74°, CPU-encoded
ShaderMotion pixels 14.70°, GPU-rendered pixels 14.84°. The pixel readouts include the
format's projection loss (Mecanim wrists have no twist axis), which is why they are higher.

Negative tests: all perturbation ladders (hand offset, head yaw, feet lift, noise; on solved
poses and on tracker inputs) degrade the score monotonically.

## Findings worth remembering

* **Scores are rest-relative** (`pose × rest⁻¹` per bone, each rig supplying its own rest) so
  rigs with different bone axes but the same T-pose compare equal.
* **Godot 4.7 headless**: cached global poses only refresh in a processing frame; modifier
  output is only visible inside `skeleton_updated`; `class_name` scripts need the project's
  class cache; nodes added in a SceneTree script's `_init` are not in the tree yet.
* **Retargeting**: Godot's importer options (`retarget/bone_map`, bone renamer, rest fixer
  with `retarget_method = 1` and silhouette fix) are driven from a generated `.import` file;
  the imported scene gets a content-hash name.
* **ShaderMotion**: zero angles are Mecanim's neutral pose, not the T-pose (a straight knee
  or elbow reads +80°); the hips position is world meters; the color curve is a continuous
  base-3 Gray curve (our encoder is continuous, the upstream Godot port's is 729-level);
  the upstream GDScript `swing_twist_inv` is unreliable (arms 90–160° off) and was replaced;
  in Godot's Compatibility renderer skinned tangents arrive garbled, so the GPU recorder uses
  only skinned normals and positions; the SubViewport image is vertically flipped and the
  darkest ~7 color levels read low.
* **Machine**: the container is 8 GB / 2 cores and shared; PID 1 (`websockify`) never reaps
  orphans, so `xvfb-run` was replaced by a reaping wrapper; every engine launch runs under a
  memory guard and tests run in parts.

## Known limitations

* RenIK's elbow/knee pole handling in the adapter costs precision (mocap 11pt hands 3.8 cm
  off); 3/4-point sets leave RenIK's legs at rest (no floor placement wired).
* The GPU ShaderMotion path is validated with the built-in IK only; RenIK's non-uniform bone
  stretch skews skinned normals slightly.
* The ShaderMotion pre/post rotation tables come from one particular avatar (1.5° residual
  on a straight profile leg); a Unity export for our own avatar would remove that.
* The hips handedness convention for Unity-encoded frames is inferred (limbs check out on a
  genuine frame; a frame of a known pose would settle it).
* Trackers reach OpenXR apps only through Monado's `XR_MNDX_xdev_space`; Godot and Unity
  expect `XR_HTCX_vive_tracker_interaction`, which Monado lacks.
