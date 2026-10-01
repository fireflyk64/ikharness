# Unity harness, avatar and shader

**Goal.** Evaluate Unity-based IK (and, through ShaderMotion, closed-source Unity apps such as
VRChat) with the same datasets and the same scorer.

**State.** Blocked: Unity is not installed on this machine. Everything below is design.

**Ready on the Godot side:** `ikh dataset export-retargeted` writes any dataset as a GLB with
the standard skeleton, a skinned body and the clip (dataset frame `i` at `i / fps` seconds),
verified to hold the reference poses exactly; trackers reach Unity's OpenXR plugin through
`XR_HTCX_vive_tracker_interaction` (Monado patch 0003); `ikh xr` shows how an application is
driven, calibrated by triggers and read off the screen.

## Pieces

1. **Test avatar** with rest rotations compatible with the standard skeleton: build a
   Unity humanoid from the dataset's T-pose joint positions (Generic rig with bone
   transforms = dataset rest, or a Humanoid avatar whose T-pose matches). Unity is
   left-handed; convert dataset (right-handed, +Z forward) by negating X.
2. **Harness (C#)**: reads `ikharness-trackers/1`, places targets, runs the IK under test
   (Unity's built-in `Animator` IK goals, or VRIK / FinalIK if available), writes
   `ikharness-result/1` with the avatar's rest, in the dataset convention.
3. **ShaderMotion unlit shader** on the avatar, rendering bone rotations as pixels; the Godot
   decoder reads screenshots. This is the only way into closed-source apps.
4. **Driver**: the Monado driver through the SteamVR plugin or the OpenVR runtime target, or
   Unity's OpenXR plugin with the HTCX extension once Monado has it.

## Feedback loop (once Unity exists)

* Harness with a "copy the reference" adapter must score 0 (validates conventions and the
  handedness flip).
* Same tracker inputs through the Unity IK and through RenIK must give comparable numbers
  for hips/head/feet (both adopt the targets), differences only in inferred joints.
