# Unity harness, avatar and shader

**Goal.** Evaluate Unity-based IK (and, through ShaderMotion, closed-source Unity apps such as
VRChat) with the same datasets and the same scorer.

**State.** Blocked: Unity is not installed on this machine. Everything below is design.

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
