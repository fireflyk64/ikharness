# T-pose calibration, shared by the harness and the XR demo. Same math as
# python/ikharness/calibration.py: from one frame of tracker poses taken while the user
# stands in the avatar's rest pose, derive the avatar root in tracking space and one rigid
# offset per tracker relative to the bone it follows.
extends RefCounted

const ROLE_BONES := {
	"head": ["Head"], "left_hand": ["LeftHand"], "right_hand": ["RightHand"], "waist": ["Hips"],
	"chest": ["UpperChest", "Chest"], "left_foot": ["LeftFoot"], "right_foot": ["RightFoot"],
	"left_knee": ["LeftLowerLeg"], "right_knee": ["RightLowerLeg"],
	"left_elbow": ["LeftLowerArm"], "right_elbow": ["RightLowerArm"],
	"left_shoulder": ["LeftUpperArm"], "right_shoulder": ["RightUpperArm"],
}

static func role_bone(skeleton: Skeleton3D, role: String) -> int:
	for name in ROLE_BONES.get(role, []):
		var b := skeleton.find_bone(name)
		if b >= 0:
			return b
	return -1

# The avatar's view point in its rest pose: eye midpoint, or the head joint.
static func eye_point(skeleton: Skeleton3D) -> Vector3:
	var le := skeleton.find_bone("LeftEye")
	var re := skeleton.find_bone("RightEye")
	if le >= 0 and re >= 0:
		return 0.5 * (skeleton.get_bone_global_rest(le).origin + skeleton.get_bone_global_rest(re).origin)
	return skeleton.get_bone_global_rest(skeleton.find_bone("Head")).origin

# Yaw from the right-to-left line of a tracker pair (independent of device axes), position
# from the headset over the avatar's eye point, feet on the floor.
static func solve_root(skeleton: Skeleton3D, tpose: Dictionary) -> Transform3D:
	var left := Vector3.ZERO
	for pair in [["left_hand", "right_hand"], ["left_foot", "right_foot"], ["left_elbow", "right_elbow"]]:
		if tpose.has(pair[0]) and tpose.has(pair[1]):
			var v: Vector3 = tpose[pair[0]].origin - tpose[pair[1]].origin
			v.y = 0.0
			if v.length() > 1e-4:
				left = v.normalized()
				break
	var fwd: Vector3
	if left == Vector3.ZERO:
		fwd = tpose["head"].basis * Vector3(0, 0, -1)  # OpenXR headsets look along -Z
		fwd.y = 0.0
		fwd = fwd.normalized()
	else:
		fwd = left.cross(Vector3.UP)  # avatar +X is its left, +Z its front
	var basis := Basis(Vector3.UP, atan2(fwd.x, fwd.z))
	var pos := Vector3.ZERO
	if tpose.has("head"):
		var eye := eye_point(skeleton)
		var off: Vector3 = basis * Vector3(eye.x, 0.0, eye.z)
		pos = Vector3(tpose["head"].origin.x - off.x, 0.0, tpose["head"].origin.z - off.z)
	return Transform3D(basis, pos)

# tpose: role -> Transform3D in tracking space.
# Returns {"root": Transform3D, "offsets": {role: Transform3D}, "bones": {role: int}, "height_ratio": float}.
static func calibrate(skeleton: Skeleton3D, tpose: Dictionary) -> Dictionary:
	var root := solve_root(skeleton, tpose)
	var offsets := {}
	var bones := {}
	for role in tpose:
		var b := role_bone(skeleton, role)
		if b < 0:
			continue
		bones[role] = b
		offsets[role] = (root * skeleton.get_bone_global_rest(b)).affine_inverse() * tpose[role]
	var ratio := 1.0
	if tpose.has("head"):
		var eye_y := eye_point(skeleton).y
		if eye_y > 1e-6:
			ratio = tpose["head"].origin.y / eye_y
	return {"root": root, "offsets": offsets, "bones": bones, "height_ratio": ratio}

# Bone pose in skeleton space for a tracker pose in tracking space.
static func bone_target(cal: Dictionary, role: String, tracker: Transform3D) -> Transform3D:
	return cal["root"].affine_inverse() * tracker * cal["offsets"][role].affine_inverse()
