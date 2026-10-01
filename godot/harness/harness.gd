# ikharness Godot harness.
#
# Builds the reference skeleton from a tracker test file, drives an IK
# implementation with the tracker poses frame by frame, and writes the
# resulting global bone poses to a result file.
#
# Usage:
#   godot --headless --path godot/harness -s harness.gd -- \
#       --trackers /abs/test.json --out /abs/result.json [--ik renik] [--settle 8] [--max-fps 240]
#       [--calibration rules|tpose]     rules: tracker offsets are given; tpose: derived from the test
#                                       file's T-pose calibration frame, as a real application would
#       [--shadermotion-dir /abs/dir]   also write each solved pose as a ShaderMotion PNG
#       [--shadermotion-gpu-dir /abs/dir]  the same through the recorder mesh + shader (needs a renderer,
#                                          e.g. xvfb-run godot --display-driver x11 --rendering-driver opengl3)
#
# --settle  number of processed frames per test pose before the pose is read,
#           so iterative / smoothed solvers converge.
extends SceneTree

const TRACKERS_FORMAT := "ikharness-trackers/1"
const RESULT_FORMAT := "ikharness-result/1"

const ShaderMotionEncoder := preload("res://shadermotion_encoder.gd")
const ShaderMotionGPU := preload("res://shadermotion_gpu.gd")
const Calibration := preload("res://calibration.gd")
const Rig := preload("res://rig.gd")

var opts := {"trackers": "", "out": "", "ik": "renik", "settle": 8, "max-fps": 240, "shadermotion-dir": "", "shadermotion-gpu-dir": "", "calibration": "rules"}

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
	Engine.max_fps = int(opts["max-fps"])
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
	if String(opts["calibration"]) == "tpose":
		if not test.has("calibration"):
			printerr("harness: --calibration tpose needs a calibration block in the test file")
			quit(1)
			return
		var tpose := {}
		for role in test["calibration"]["trackers"]:
			tpose[role] = xform_of(test["calibration"]["trackers"][role])
		calibration = Calibration.calibrate(skeleton, tpose)
		var root: Transform3D = calibration["root"]
		print("harness: T-pose calibration, root yaw %.2f deg at (%.3f, %.3f), height ratio %.3f, %d trackers" % [
			rad_to_deg(root.basis.get_euler().y), root.origin.x, root.origin.z, calibration["height_ratio"], calibration["offsets"].size()])
	elif String(opts["calibration"]) != "rules":
		printerr("harness: unknown --calibration ", opts["calibration"])
		quit(1)
		return
	if opts["shadermotion-gpu-dir"] != "":
		if DisplayServer.get_name() == "headless":
			printerr("harness: --shadermotion-gpu-dir needs a renderer; run without --headless (xvfb-run ... --rendering-driver opengl3)")
			quit(1)
			return
		ShaderMotionGPU.build_recorder(skeleton, float(test["skeleton"]["hips_height"]))
		gpu_viewport = ShaderMotionGPU.make_viewport(Vector2i(640, 360))
		get_root().add_child(gpu_viewport)

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
			opts[key] = int(v) if key in ["settle", "max-fps"] else v
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

func build_skeleton(sk: Dictionary) -> Skeleton3D:
	var built := Rig.build_skeleton(sk)
	humanoid_bone_ids = built[1]
	return built[0]

func tracker_xform(frame: Dictionary, role: String) -> Transform3D:
	return xform_of(frame["trackers"][role])

# Turn a tracker pose into the pose of the bone it is attached to, by undoing
# the rule offset (what a calibration with trackers placed on the bones yields).
func bone_target_xform(frame: Dictionary, role: String) -> Transform3D:
	if not calibration.is_empty():
		return Calibration.bone_target(calibration, role, tracker_xform(frame, role))
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
var last_globals: Dictionary = {}
var gpu_viewport: SubViewport = null
var calibration: Dictionary = {}

func _on_skeleton_updated() -> void:
	var bones := {}
	var globals := {"Root": Transform3D.IDENTITY}
	for b in humanoid_bone_ids:
		var g := skeleton.get_bone_global_pose(b)
		globals[skeleton.get_bone_name(b)] = g
		var q := g.basis.get_rotation_quaternion().normalized()
		bones[skeleton.get_bone_name(b)] = {
			"position": [g.origin.x, g.origin.y, g.origin.z],
			"rotation": [q.x, q.y, q.z, q.w],
		}
	last_captured = bones
	last_globals = globals

func write_shadermotion_frame(index: int) -> void:
	var dir: String = opts["shadermotion-dir"]
	if dir == "" or last_globals.is_empty():
		return
	var slots := ShaderMotionEncoder.pose_to_slots(skeleton, last_globals, float(test["skeleton"]["hips_height"]))
	var img := ShaderMotionEncoder.slots_to_image(slots)
	img.save_png(dir.path_join("frame_%05d.png" % index))

# The SubViewport holds the render of the previous frame, which used the pose captured in
# last_globals (targets are constant while settling), so image and JSON describe one pose.
func write_shadermotion_gpu_frame(index: int) -> void:
	if gpu_viewport == null:
		return
	var img := gpu_viewport.get_texture().get_image()
	img.convert(Image.FORMAT_RGB8)
	img.save_png(String(opts["shadermotion-gpu-dir"]).path_join("frame_%05d.png" % index))

func read_global_poses() -> Dictionary:
	return last_captured

# Called by the stepper every processed frame, after the skeleton (and its
# modifiers) ran for the previous frame.
func step() -> void:
	var frames: Array = test["frames"]
	if adapter.busy():
		return   # e.g. waiting for a physics tick; settle frames count from when it is done
	if frame_index >= 0 and settle_left > 0:
		settle_left -= 1
		if settle_left == 0:
			var frame: Dictionary = frames[frame_index]
			var bones := read_global_poses()
			results.append({"index": frame["index"], "time": frame.get("time", 0.0), "bones": bones})
			write_shadermotion_frame(int(frame["index"]))
			write_shadermotion_gpu_frame(int(frame["index"]))
			if OS.get_environment("IKH_DEBUG") != "" and adapter.get("placement") != null:
				for side in ["left_foot", "right_foot"]:
					var bn: String = "LeftFoot" if side == "left_foot" else "RightFoot"
					var kn: String = "LeftLowerLeg" if side == "left_foot" else "RightLowerLeg"
					print("debug placed %s: target %s / foot bone %s / knee %s / walk_state %s" % [side, adapter.targets[side].global_position,
						bones[bn]["position"], bones[kn]["position"], adapter.placement.walk_state])
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
		"calibration": opts["calibration"],
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
	# True while the adapter still has to react to the last apply_frame (the harness waits).
	func busy() -> bool:
		return false


# RenIK (GDScript port, addons/renik): spine solver with head/hip/chest
# targets and trig limb solvers with optional pole targets.
class RenIKAdapter extends IKAdapter:
	var targets := {}
	var spine_ik
	var limbs := {}
	var poles := {}          # limb role -> [pole node, elbow/knee role]
	var chest_id := -1
	var placement = null     # RenIKPlacement3D when the feet are not tracked (3 / 4 point sets)
	var placement_pending := false
	# The harness shows unrelated poses one after another, so feet are placed at once; a live
	# application sets this to false and gets RenIK's stepping gait.
	var instant_placement := true
	var placed := {}         # foot role -> node RenIK placement writes to
	var foot_fix := {}       # foot role -> rotation from RenIK's flat foot to this rig's foot bone
	var skeleton: Skeleton3D

	func implementation_name() -> String:
		return "renik"

	func _marker(root: Node3D, name: String) -> Node3D:
		var m := Marker3D.new()
		m.name = name
		root.add_child(m)
		return m

	func setup(root: Node3D, skel: Skeleton3D, test: Dictionary) -> void:
		var roles: Array = test["roles"]
		skeleton = skel
		for role in ["head", "waist", "chest", "left_hand", "right_hand", "left_foot", "right_foot", "left_elbow", "right_elbow", "left_knee", "right_knee"]:
			targets[role] = _marker(root, role.capitalize().replace(" ", "") + "Target")
			targets[role].visible = roles.has(role)

		var uchest := skel.find_bone("UpperChest")
		var chest_bone := skel.get_bone_name(skel.find_bone("Chest")) if uchest != -1 else skel.get_bone_name(skel.find_bone("Spine"))
		chest_id = skel.find_bone(chest_bone)

		spine_ik = RenIKSpineModifier3D.new()
		spine_ik.name = "SpineIK"
		spine_ik.root_bone = &"Hips"
		spine_ik.leaf_bone = &"Head"
		spine_ik.chest_bone = StringName(chest_bone)
		spine_ik.head_target = targets["head"]
		spine_ik.hip_target = targets["waist"]
		spine_ik.chest_target = targets["chest"]
		skel.add_child(spine_ik)

		# Without foot trackers RenIK places the feet itself (raycasts onto the floor below the
		# head, a stepping gait when moving) and, without a waist tracker, the hips too.
		# IKH_RENIK_PLACEMENT=0 leaves the legs in their rest pose instead.
		var place_feet: bool = not (roles.has("left_foot") and roles.has("right_foot")) and OS.get_environment("IKH_RENIK_PLACEMENT") != "0"
		if place_feet:
			var floor_body := StaticBody3D.new()
			floor_body.name = "Floor"
			var shape := CollisionShape3D.new()
			shape.shape = WorldBoundaryShape3D.new()
			floor_body.add_child(shape)
			root.add_child(floor_body)
			placement = RenIKPlacement3D.new()
			placement.name = "Placement"
			placement.enable_left_foot_placement = true
			placement.enable_right_foot_placement = true
			placement.enable_hip_placement = not roles.has("waist")
			# RenIK puts the hips at crouch_ratio of the head-to-foot distance below the head; its
			# default (0.4) fits its own test avatar. Use this avatar's standing proportions.
			var head_y := skel.get_bone_global_rest(skel.find_bone("Head")).origin.y
			var hips_y := skel.get_bone_global_rest(skel.find_bone("Hips")).origin.y
			var foot_y := skel.get_bone_global_rest(skel.find_bone("LeftFoot")).origin.y
			placement.crouch_ratio = (head_y - hips_y) / maxf(head_y - foot_y, 0.01)
			root.add_child(placement)
			placement.armature_skeleton_path = placement.get_path_to(skel)
			placement.armature_head_target = placement.get_path_to(targets["head"])
			placement.armature_hip_target = placement.get_path_to(targets["waist"])
			# RenIK places a foot as a bone lying flat and pointing forward (foot_basis_offset); in
			# the humanoid profile the foot bone points down to the toes. Placement writes to raw
			# nodes and the limb targets get the rest pitch added (copy_placed_feet).
			for side in ["left_foot", "right_foot"]:
				placed[side] = _marker(root, side.capitalize().replace(" ", "") + "Placed")
				var rest: Basis = skel.get_bone_global_rest(skel.find_bone("LeftFoot" if side == "left_foot" else "RightFoot")).basis.orthonormalized()
				foot_fix[side] = placement.foot_basis_offset.inverse() * rest
			placement.armature_left_foot_target = placement.get_path_to(placed["left_foot"])
			placement.armature_right_foot_target = placement.get_path_to(placed["right_foot"])
			targets["left_foot"].visible = true
			targets["right_foot"].visible = true
			if placement.enable_hip_placement:
				targets["waist"].visible = true
			var driver := PlacementDriver.new()
			driver.name = "PlacementDriver"
			driver.adapter = self
			root.add_child(driver)

		for spec in [["left_hand", RenIKLimbModifier3D.LEFT_HAND, true, "left_elbow"],
					 ["right_hand", RenIKLimbModifier3D.RIGHT_HAND, true, "right_elbow"],
					 ["left_foot", RenIKLimbModifier3D.LEFT_FOOT, false, "left_knee"],
					 ["right_foot", RenIKLimbModifier3D.RIGHT_FOOT, false, "right_knee"]]:
			var role: String = spec[0]
			if not roles.has(role) and not (place_feet and not spec[2]):
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
				var pole := _marker(root, role.capitalize().replace(" ", "") + "Pole")
				limb.pole_target = pole
				poles[role] = [pole, spec[3]]
			skel.add_child(limb)
			limbs[role] = limb

	func busy() -> bool:
		return placement_pending

	func copy_placed_feet() -> void:
		for side in placed:
			var raw: Transform3D = placed[side].global_transform
			targets[side].global_transform = Transform3D(raw.basis.orthonormalized() * foot_fix[side], raw.origin)

	# Called on a physics tick (raycasts need one): feet, then hips, straight to their places.
	func place_now(delta: float) -> void:
		placement_pending = false
		var head: Transform3D = targets["head"].global_transform
		placement.prevHead = head.origin     # a new pose, not a movement: no velocity, no stride
		placement.foot_place(delta, head, skeleton.get_world_3d(), true)
		placement.target_foot_is_valid = true
		if placement.enable_hip_placement:
			placement.hip_place(delta, head, placement.target_left_foot, placement.target_right_foot, 0.0, true)
			placement.target_hip_is_valid = true
		placement.save_previous_transforms()
		placement.interpolate_transforms(1.0)
		copy_placed_feet()
		if OS.get_environment("IKH_DEBUG") != "":
			print("debug placement: head %s -> left foot %s right foot %s hips %s (legs %.3f/%.3f spine %.3f hip_offset %s)" % [
				head.origin, placement.target_left_foot.origin, placement.target_right_foot.origin, placement.target_hip.origin,
				placement.left_leg_length, placement.right_leg_length, placement.spine_length, placement.hip_offset])
			print("debug placement: hip offsets %s %s" % [placement.left_hip_offset, placement.right_hip_offset])

	func apply_frame(h: SceneTree, frame: Dictionary) -> void:
		var harness = h
		for role in targets.keys():
			if harness.has_tracker(role):
				targets[role].global_transform = harness.bone_target_xform(frame, role)
		if placement != null and instant_placement:
			placement_pending = true
		# RenIK's spine solver adds (chest target - the chest bone's pose before solving) to the
		# head target, which only makes sense when the skeleton node itself follows the player.
		# Ours stands still while the pose moves, so hand it the chest's orientation only.
		if harness.has_tracker("chest") and chest_id >= 0:
			var xf: Transform3D = targets["chest"].global_transform
			targets["chest"].global_transform = Transform3D(xf.basis, skeleton.global_transform * skeleton.get_bone_global_pose(chest_id).origin)
		# RenIK's pole target is a direction, not a point to bend towards: the limb bends in the
		# plane through its root, its target and the point 1000 m along the pole node's +Z, turned
		# about Y by the limb's twist offset. An elbow / knee tracker gives the lower bone's
		# orientation, and in the humanoid profile the hinge axis of every limb is the lower
		# bone's local X (measured on four datasets), so the in-plane direction is its -Z. For
		# legs RenIK's own offset (pi) already yields -Z; for arms (-pi/2) it yields the hinge
		# axis itself, which left the bend plane undefined: turn the node to compensate.
		for role in poles:
			var limb = limbs[role]
			var lower: Transform3D = harness.bone_target_xform(frame, poles[role][1])
			var renik_dir: Vector3 = Quaternion(Vector3.UP, -limb.mirror_factor * limb.lower_twist_offset) * Vector3(0, 0, 1)
			var fix := Basis(Quaternion(renik_dir, Vector3(0, 0, -1))) if renik_dir.dot(Vector3(0, 0, -1)) < 0.999 else Basis.IDENTITY
			poles[role][0].global_transform = Transform3D(lower.basis.orthonormalized() * fix, lower.origin)


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
	var skeleton: Skeleton3D

	func implementation_name() -> String:
		return "builtin"

	func _marker(root: Node3D, name: String) -> Node3D:
		var m := Marker3D.new()
		m.name = name
		root.add_child(m)
		return m

	func setup(root: Node3D, skel: Skeleton3D, test: Dictionary) -> void:
		roles = test["roles"]
		skeleton = skel
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
		# Pole nodes: elbow/knee trackers when tracked, otherwise behind the elbows and in front of the knees
		# (in the avatar's frame: the skeleton may stand anywhere in the room, facing any way).
		var facing := skeleton.global_transform.basis.orthonormalized()
		var pole_roles := {"left_hand": "left_elbow", "right_hand": "right_elbow", "left_foot": "left_knee", "right_foot": "right_knee"}
		for limb in poles.keys():
			var pr: String = pole_roles[limb]
			if harness.has_tracker(pr):
				poles[limb].global_position = harness.bone_target_xform(frame, pr).origin
			elif harness.has_tracker(limb):
				var t: Vector3 = targets[limb].global_position
				if limb.ends_with("hand"):
					poles[limb].global_position = t + facing * Vector3(0.0, -0.1, -0.5)
				else:
					poles[limb].global_position = t + facing * Vector3(0.0, 0.5, 0.6)


class PlacementDriver extends Node:
	var adapter
	func _init() -> void:
		process_priority = 100    # after RenIK placement interpolated its targets for this frame
	func _physics_process(delta: float) -> void:
		if adapter.placement_pending:
			adapter.place_now(delta)
	func _process(_delta: float) -> void:
		adapter.copy_placed_feet()


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
