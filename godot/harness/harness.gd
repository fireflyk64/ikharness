# ikharness Godot harness.
#
# Builds the reference skeleton from a tracker test file, drives an IK
# implementation with the tracker poses frame by frame, and writes the
# resulting global bone poses to a result file.
#
# Usage:
#   godot --headless --path godot/harness -s harness.gd -- \
#       --trackers /abs/test.json --out /abs/result.json [--ik renik] [--settle 8]
#
# --settle  number of processed frames per test pose before the pose is read,
#           so iterative / smoothed solvers converge.
extends SceneTree

const TRACKERS_FORMAT := "ikharness-trackers/1"
const RESULT_FORMAT := "ikharness-result/1"

var opts := {"trackers": "", "out": "", "ik": "renik", "settle": 8}

var test: Dictionary
var skeleton: Skeleton3D
var adapter: IKAdapter
var frame_index := -1
var settle_left := 0
var results: Array = []
var scene_root: Node3D
var humanoid_bone_ids: Array[int] = []

func _init():
	_parse_args()
	if opts["trackers"] == "" or opts["out"] == "":
		printerr("harness: --trackers and --out are required")
		quit(1)
		return
	var f := FileAccess.open(opts["trackers"], FileAccess.READ)
	if f == null:
		printerr("harness: cannot read ", opts["trackers"])
		quit(1)
		return
	test = JSON.parse_string(f.get_as_text())
	f.close()
	if typeof(test) != TYPE_DICTIONARY or test.get("format", "") != TRACKERS_FORMAT:
		printerr("harness: not a ", TRACKERS_FORMAT, " file")
		quit(1)
		return

	scene_root = Node3D.new()
	scene_root.name = "Harness"
	get_root().add_child(scene_root)
	skeleton = build_skeleton(test["skeleton"])
	scene_root.add_child(skeleton)
	skeleton.skeleton_updated.connect(_on_skeleton_updated)

	match String(opts["ik"]):
		"renik":
			adapter = RenIKAdapter.new()
		"builtin":
			adapter = BuiltinAdapter.new()
		"none":
			adapter = IKAdapter.new()
		_:
			printerr("harness: unknown --ik ", opts["ik"])
			quit(1)
			return
	adapter.setup(scene_root, skeleton, test)
	var stepper := HarnessStepper.new()
	stepper.name = "Stepper"
	stepper.harness = self
	scene_root.add_child(stepper)

func _parse_args() -> void:
	var args := OS.get_cmdline_user_args()
	var i := 0
	while i < args.size():
		var a: String = args[i]
		if a.begins_with("--") and i + 1 < args.size():
			var key := a.substr(2)
			var v: String = args[i + 1]
			opts[key] = int(v) if key == "settle" else v
			i += 1
		i += 1

static func pos_of(d: Dictionary) -> Vector3:
	var p: Array = d["position"]
	return Vector3(p[0], p[1], p[2])

static func rot_of(d: Dictionary) -> Quaternion:
	var r: Array = d["rotation"]
	return Quaternion(r[0], r[1], r[2], r[3]).normalized()

static func xform_of(d: Dictionary) -> Transform3D:
	return Transform3D(Basis(rot_of(d)), pos_of(d))

# Skeleton3D from the test file's skeleton block. Humanoid bones only; a Root
# bone is inserted above Hips when the dataset has none, because most solvers
# expect Hips to have a parent.
func build_skeleton(sk: Dictionary) -> Skeleton3D:
	var skel := Skeleton3D.new()
	skel.name = "GeneralSkeleton"
	var bones: Array = sk["bones"]
	var names := {}
	for b in bones:
		names[b["name"]] = true
	var has_root := names.has("Root")
	if not has_root:
		skel.add_bone("Root")
		skel.set_bone_rest(0, Transform3D.IDENTITY)
	for b in bones:
		skel.add_bone(b["name"])
	for b in bones:
		var idx := skel.find_bone(b["name"])
		var parent: String = b.get("parent", "")
		var pidx := skel.find_bone(parent) if parent != "" else (skel.find_bone("Root") if not has_root else -1)
		skel.set_bone_parent(idx, pidx)
		skel.set_bone_rest(idx, xform_of(b["rest_local"]))
		humanoid_bone_ids.append(idx)
	skel.reset_bone_poses()
	return skel

func tracker_xform(frame: Dictionary, role: String) -> Transform3D:
	return xform_of(frame["trackers"][role])

# Turn a tracker pose into the pose of the bone it is attached to, by undoing
# the rule offset (what a calibration with trackers placed on the bones yields).
func bone_target_xform(frame: Dictionary, role: String) -> Transform3D:
	var rule: Dictionary = test["rules"][role]
	var off: Array = rule["offset"]
	var rot: Array = rule["rotation"]
	var offset := Transform3D(Basis(Quaternion(rot[0], rot[1], rot[2], rot[3]).normalized()), Vector3(off[0], off[1], off[2]))
	return tracker_xform(frame, role) * offset.affine_inverse()

func has_tracker(role: String) -> bool:
	return test["roles"].has(role)

# Skeleton3D applies its SkeletonModifier3D children during its own update
# (after the frame's _process callbacks) and restores the unmodified poses
# right after, so the solved pose is only observable inside skeleton_updated.
# Capture it there; step() then records the capture once the settle frames
# for the current targets have elapsed.
var last_captured: Dictionary = {}

func _on_skeleton_updated() -> void:
	var bones := {}
	for b in humanoid_bone_ids:
		var g := skeleton.get_bone_global_pose(b)
		var q := g.basis.get_rotation_quaternion().normalized()
		bones[skeleton.get_bone_name(b)] = {
			"position": [g.origin.x, g.origin.y, g.origin.z],
			"rotation": [q.x, q.y, q.z, q.w],
		}
	last_captured = bones

func read_global_poses() -> Dictionary:
	return last_captured

# Called by the stepper every processed frame, after the skeleton (and its
# modifiers) ran for the previous frame.
func step() -> void:
	var frames: Array = test["frames"]
	if frame_index >= 0 and settle_left > 0:
		settle_left -= 1
		if settle_left == 0:
			var frame: Dictionary = frames[frame_index]
			var bones := read_global_poses()
			results.append({"index": frame["index"], "time": frame.get("time", 0.0), "bones": bones})
			if frame_index == 0 and OS.get_environment("IKH_DEBUG") != "":
				for role in test["roles"]:
					var rule: Dictionary = test["rules"][role]
					var want := bone_target_xform(frame, role)
					var got: Dictionary = bones[rule["bone"]]
					print("debug frame0 %s -> %s: target %s / got %s" % [role, rule["bone"], want.origin, Vector3(got["position"][0], got["position"][1], got["position"][2])])
	if settle_left > 0:
		return
	frame_index += 1
	if frame_index >= frames.size():
		finish()
		return
	adapter.apply_frame(self, frames[frame_index])
	settle_left = max(1, int(opts["settle"]))

func rest_block() -> Dictionary:
	var bones: Array = []
	for b in humanoid_bone_ids:
		var g := skeleton.get_bone_global_rest(b)
		var q := g.basis.get_rotation_quaternion().normalized()
		bones.append({
			"name": skeleton.get_bone_name(b),
			"rest_global": {"position": [g.origin.x, g.origin.y, g.origin.z], "rotation": [q.x, q.y, q.z, q.w]},
		})
	return {"convention": test["skeleton"].get("convention", ""), "bones": bones}

func finish() -> void:
	var doc := {
		"format": RESULT_FORMAT,
		"skeleton": rest_block(),
		"implementation": adapter.implementation_name(),
		"tracker_set": test.get("tracker_set", ""),
		"roles": test.get("roles", []),
		"settle_frames": opts["settle"],
		"godot": Engine.get_version_info()["string"],
		"frames": results,
	}
	var f := FileAccess.open(opts["out"], FileAccess.WRITE)
	f.store_string(JSON.stringify(doc))
	f.close()
	print("harness: wrote %d frames (%s, %s) to %s" % [results.size(), adapter.implementation_name(), test.get("tracker_set", ""), opts["out"]])
	quit(0)


# Drives step() from the scene tree's process loop. Skeleton modifiers run in
# Skeleton3D's own deferred update, which happens after all _process calls of
# a frame, so reading in the *next* frame's _process sees settled bones.
class HarnessStepper extends Node:
	var harness: SceneTree
	func _process(_delta: float) -> void:
		harness.step()


# Base adapter: no IK, the skeleton stays in rest (useful as a baseline).
class IKAdapter extends RefCounted:
	func implementation_name() -> String:
		return "none"
	func setup(_root: Node3D, _skel: Skeleton3D, _test: Dictionary) -> void:
		pass
	func apply_frame(_h: SceneTree, _frame: Dictionary) -> void:
		pass


# RenIK (GDScript port, addons/renik): spine solver with head/hip/chest
# targets and trig limb solvers with optional pole targets.
class RenIKAdapter extends IKAdapter:
	var targets := {}
	var spine_ik
	var limbs := {}

	func implementation_name() -> String:
		return "renik"

	func _marker(root: Node3D, name: String) -> Node3D:
		var m := Marker3D.new()
		m.name = name
		root.add_child(m)
		return m

	func setup(root: Node3D, skel: Skeleton3D, test: Dictionary) -> void:
		var roles: Array = test["roles"]
		for role in ["head", "waist", "chest", "left_hand", "right_hand", "left_foot", "right_foot", "left_elbow", "right_elbow", "left_knee", "right_knee"]:
			targets[role] = _marker(root, role.capitalize().replace(" ", "") + "Target")
			targets[role].visible = roles.has(role)

		var uchest := skel.find_bone("UpperChest")
		var chest_bone := skel.get_bone_name(skel.find_bone("Chest")) if uchest != -1 else skel.get_bone_name(skel.find_bone("Spine"))

		spine_ik = RenIKSpineModifier3D.new()
		spine_ik.name = "SpineIK"
		spine_ik.root_bone = &"Hips"
		spine_ik.leaf_bone = &"Head"
		spine_ik.chest_bone = StringName(chest_bone)
		spine_ik.head_target = targets["head"]
		spine_ik.hip_target = targets["waist"]
		spine_ik.chest_target = targets["chest"]
		skel.add_child(spine_ik)

		for spec in [["left_hand", RenIKLimbModifier3D.LEFT_HAND, true, "left_elbow"],
					 ["right_hand", RenIKLimbModifier3D.RIGHT_HAND, true, "right_elbow"],
					 ["left_foot", RenIKLimbModifier3D.LEFT_FOOT, false, "left_knee"],
					 ["right_foot", RenIKLimbModifier3D.RIGHT_FOOT, false, "right_knee"]]:
			var role: String = spec[0]
			if not roles.has(role):
				continue
			var limb := RenIKLimbModifier3D.new()
			limb.name = role.capitalize().replace(" ", "")
			limb.preset = spec[1]
			if spec[2]:
				limb.assign_arm_defaults.call()
				limb.stretchiness = 0.25
			else:
				limb.assign_leg_defaults.call()
			limb.target = targets[role]
			if roles.has(spec[3]):
				limb.pole_target = targets[spec[3]]
			skel.add_child(limb)
			limbs[role] = limb

	func apply_frame(h: SceneTree, frame: Dictionary) -> void:
		var harness = h
		for role in targets.keys():
			if harness.has_tracker(role):
				targets[role].global_transform = harness.bone_target_xform(frame, role)


# Godot's built-in modifiers: FABRIK3D for the spine chain, TwoBoneIK3D for
# arms and legs (pole nodes from elbow/knee trackers when present, else a
# heuristic behind the elbow / in front of the knee), a custom modifier that
# places the hips from the waist tracker (or under the head), and a final
# modifier that copies tracker orientations onto the end effectors.
class BuiltinAdapter extends IKAdapter:
	var targets := {}
	var poles := {}
	var roles: Array = []
	var hips_mod: HipsModifier

	func implementation_name() -> String:
		return "builtin"

	func _marker(root: Node3D, name: String) -> Node3D:
		var m := Marker3D.new()
		m.name = name
		root.add_child(m)
		return m

	func setup(root: Node3D, skel: Skeleton3D, test: Dictionary) -> void:
		roles = test["roles"]
		for role in ["head", "waist", "chest", "left_hand", "right_hand", "left_foot", "right_foot", "left_elbow", "right_elbow", "left_knee", "right_knee"]:
			targets[role] = _marker(root, role.capitalize().replace(" ", "") + "Target")
			targets[role].visible = roles.has(role)
		for limb in ["left_hand", "right_hand", "left_foot", "right_foot"]:
			poles[limb] = _marker(root, limb.capitalize().replace(" ", "") + "Pole")

		# 1. Hips.
		hips_mod = HipsModifier.new()
		hips_mod.name = "Hips"
		hips_mod.target = targets["waist"]
		hips_mod.head_target = targets["head"]
		hips_mod.hips_bone = skel.find_bone("Hips")
		var head_rest := skel.get_bone_global_rest(skel.find_bone("Head")).origin
		var hips_rest := skel.get_bone_global_rest(hips_mod.hips_bone).origin
		hips_mod.rest_offset_from_head = hips_rest - head_rest
		skel.add_child(hips_mod)

		# 2. Spine chain Spine -> Head reaching the head target.
		var spine := FABRIK3D.new()
		spine.name = "Spine"
		spine.max_iterations = 16
		spine.min_distance = 0.0005
		spine.set_setting_count(1)
		spine.set_root_bone_name(0, &"Spine")
		spine.set_end_bone_name(0, &"Head")
		skel.add_child(spine)
		spine.set_target_node(0, spine.get_path_to(targets["head"]))

		# 3. Limbs.
		var limbs := TwoBoneIK3D.new()
		limbs.name = "Limbs"
		var specs := [
			["left_hand", &"LeftUpperArm", &"LeftLowerArm", &"LeftHand"],
			["right_hand", &"RightUpperArm", &"RightLowerArm", &"RightHand"],
			["left_foot", &"LeftUpperLeg", &"LeftLowerLeg", &"LeftFoot"],
			["right_foot", &"RightUpperLeg", &"RightLowerLeg", &"RightFoot"],
		]
		var active := []
		for sp in specs:
			if roles.has(sp[0]):
				active.append(sp)
		limbs.set_setting_count(active.size())
		skel.add_child(limbs)
		for i in range(active.size()):
			var sp: Array = active[i]
			limbs.set_root_bone_name(i, sp[1])
			limbs.set_middle_bone_name(i, sp[2])
			limbs.set_end_bone_name(i, sp[3])
			limbs.set_target_node(i, limbs.get_path_to(targets[sp[0]]))
			limbs.set_pole_node(i, limbs.get_path_to(poles[sp[0]]))

		# 4. End effector orientations from the trackers.
		var ee := EndEffectorRotationModifier.new()
		ee.name = "EndEffectors"
		for pair in [["head", "Head"], ["left_hand", "LeftHand"], ["right_hand", "RightHand"], ["left_foot", "LeftFoot"], ["right_foot", "RightFoot"]]:
			if roles.has(pair[0]):
				ee.pairs.append([skel.find_bone(pair[1]), targets[pair[0]]])
		skel.add_child(ee)

	func apply_frame(h: SceneTree, frame: Dictionary) -> void:
		var harness = h
		for role in targets.keys():
			if harness.has_tracker(role):
				targets[role].global_transform = harness.bone_target_xform(frame, role)
		# Pole nodes: elbow/knee trackers when tracked, otherwise behind the elbows and in front of the knees.
		var pole_roles := {"left_hand": "left_elbow", "right_hand": "right_elbow", "left_foot": "left_knee", "right_foot": "right_knee"}
		for limb in poles.keys():
			var pr: String = pole_roles[limb]
			if harness.has_tracker(pr):
				poles[limb].global_position = harness.bone_target_xform(frame, pr).origin
			elif harness.has_tracker(limb):
				var t: Vector3 = targets[limb].global_position
				if limb.ends_with("hand"):
					poles[limb].global_position = t + Vector3(0.0, -0.1, -0.5)
				else:
					poles[limb].global_position = t + Vector3(0.0, 0.5, 0.6)


class HipsModifier extends SkeletonModifier3D:
	var target: Node3D
	var head_target: Node3D
	var hips_bone: int = -1
	var rest_offset_from_head: Vector3

	func _process_modification() -> void:
		var s := get_skeleton()
		if s == null or hips_bone < 0:
			return
		var inv := s.global_transform.affine_inverse()
		if target != null and target.visible:
			s.set_bone_global_pose(hips_bone, (inv * target.global_transform).orthonormalized())
		elif head_target != null and head_target.visible:
			var head := (inv * head_target.global_transform).orthonormalized()
			var fwd: Vector3 = head.basis * Vector3(0, 0, 1)
			fwd.y = 0.0
			if fwd.length() < 1e-4:
				fwd = Vector3(0, 0, 1)
			var yaw := Basis.looking_at(-fwd.normalized(), Vector3.UP)
			s.set_bone_global_pose(hips_bone, Transform3D(yaw, head.origin + yaw * rest_offset_from_head))


class EndEffectorRotationModifier extends SkeletonModifier3D:
	var pairs: Array = []

	func _process_modification() -> void:
		var s := get_skeleton()
		if s == null:
			return
		var inv := s.global_transform.affine_inverse()
		for p in pairs:
			var node: Node3D = p[1]
			if node == null or not node.visible:
				continue
			var g := s.get_bone_global_pose(p[0])
			var t := (inv * node.global_transform).orthonormalized()
			s.set_bone_global_pose(p[0], Transform3D(t.basis, g.origin))
