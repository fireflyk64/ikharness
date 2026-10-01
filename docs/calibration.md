# T-pose calibration

**Goal.** Make calibration part of the test instead of something the harness is told. The
reference rig strikes the avatar's T-pose, holds it for a second, and both triggers are
pulled; the application derives where the avatar stands and how every tracker is attached.

**State.** Working in the Godot harness (`--calibration tpose`), as a remote gesture through
the Monado driver (`ikh calibrate`, `ikh replay --calibrate`), and in the XR demo.

## The math (`python/ikharness/calibration.py`, `godot/harness/calibration.gd`)

From the one calibration frame of tracker poses `T_role`:

1. **Root yaw** from the right-to-left line of a tracker pair (hands, else feet, else
   elbows): `forward = left × up`. No device's local axes are involved, so it works for any
   controller or tracker convention. With only a headset, its −Z view direction is used.
2. **Root position**: the headset sits over the avatar's eye point (eye-bone midpoint; for a
   rig without eye bones 8 cm above and 9 cm in front of the head joint, `DEFAULT_VIEW_OFFSET`,
   the same point the head tracker rule uses), feet on the floor (`y = 0`).
   `height_ratio = headset height / avatar eye height` is reported.
   A wrong view point is not harmless: the root ends up `d` meters off, and every bone
   target is then conjugated by that offset, an error of up to `2d` once the user turns
   around. On the Perfume dance (no eye bones) a 9 cm mismatch cost 9° at 11 points.
3. **Offsets**: `offset_role = (root · bone_rest)⁻¹ · T_role`, one rigid transform per tracker.
4. Every later frame: `bone_target = root⁻¹ · tracker · offset⁻¹` in avatar space.

A T-pose that deviates from the avatar's rest pose becomes error: a root position error `e`
moves rotated bone targets by up to `2|e|`. The reference rig strikes the pose exactly, so
the harness measures the IK, not the calibration; perturbing the calibration frame is a
future negative test.

## Run

```sh
ikh eval --dataset D --tracker-set 11pt --ik builtin --calibration tpose      # harness derives offsets itself
ikh eval ... --perturb tracker_mount:0.08                                     # body trackers strapped on 8 cm / 30° off
ikh eval ... --perturb tracker_mount:0.08 --calibration tpose                 # ... and calibrated away

ikh service &
ikh calibrate --dataset D --tracker-set 6pt            # T-pose, hold 1 s, pull both triggers, release
ikh replay --dataset D --tracker-set 6pt --calibrate   # the same, then the frames
```

The gesture (`run_calibration_gesture`): release triggers → send the T-pose frame → hold
(`--calibrate-hold`, default 1 s) → `trigger = 1.0` on both controllers for 0.3 s → release.
For OpenXR the headset pose is turned so it looks along its own −Z where the avatar faces
(`trackers.to_openxr_device`); controllers and body trackers keep the bone frame because
applications calibrate them.

## Feedback loop

* `tests/test_calibration.py`: a user standing anywhere, turned by any yaw, with body
  trackers mounted arbitrarily still yields exact bone targets; the OpenXR convention gives a
  root turned by 180°.
* `tests/test_godot_harness.py::test_tpose_calibration_matches_rules_and_absorbs_mounting`:
  `tpose` equals `rules` within 0.01°; `tracker_mount:0.08` costs more than 2° uncalibrated
  and nothing once calibrated.
* `tests/test_monado_driver.py::test_calibration_gesture_reaches_openxr`: an OpenXR client
  sees the T-pose first, then both triggers rise together, then release.
