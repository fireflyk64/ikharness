# Godot as an OpenXR client of the Monado driver

**Goal.** Test Godot's own XR path end to end: the Monado driver publishes trackers, a
Godot scene reads them through `OpenXRInterface`, drives an avatar, and the harness reads
the bones back. This covers what a real Godot VR game does, including its tracker
bindings and calibration.

**State.** Planned. Blocked on tracker delivery: Godot reads generic trackers through
`XR_HTCX_vive_tracker_interaction` (`XRNode3D` with tracker names like `left_foot_tracker`),
which Monado's OpenXR layer does not implement (`ALWAYS_DISABLED` in
`oxr_extension_support.py`). Head and hands already work (VIEW space, Index controller
profile).

## Plan

1. Implement `XR_HTCX_vive_tracker_interaction` in Monado's OpenXR state tracker:
   subaction paths `/user/vive_tracker_htcx/role/<role>`, the interaction profile from
   `bindings.json` (already present), `xrEnumerateViveTrackerPathsHTCX`, and role assignment
   from the driver's tracker roles. Keep it as a patch in `monado/patches/`.
2. Godot scene `godot/xrclient/`: `XROrigin3D`, `XRCamera3D`, `XRController3D` × 2,
   `XRNode3D` per tracker role, a humanoid avatar, and the same adapter interface as the
   harness (RenIK first). Reads OpenXR each frame; writes result files.
3. Driver: `ikh replay` with `--dwell` long enough for settle; the Godot client records the
   frame id from a side channel (a tiny TCP/UDP message from the replay tool, since the
   frame id cannot travel through OpenXR).
4. Alternative without the Monado extension work: `RetargetModifier3D` driven directly by
   the replay tool over the wire protocol inside Godot (no OpenXR), which tests the avatar
   path but not the runtime.

## Feedback loop

* `ikh probe` shows the same poses `ikh replay` sent (0 mm).
* The Godot client with `--ik none` must reproduce the tracker poses on the corresponding
  bones (score of the tracked bones ≈ 0), before any IK is enabled.
* Then the Godot client with RenIK must match `ikh eval --ik renik` within a degree (same
  solver, same inputs, different transport).
