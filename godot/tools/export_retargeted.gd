# Godot as preprocessor: write a reference dataset as files other engines can import.
#
#   godot --headless --path godot/tools -s export_retargeted.gd -- \
#       --dataset /abs/dataset.json --out /abs/clip.glb [--fps 2] [--scene /abs/clip.tscn] [--animation /abs/clip.res] [--no-mesh]
#
# --no-mesh leaves the body shapes out of the Godot scene; the GLB always has them, because
# glTF stores a skeleton as the skin of a mesh.
#
# The output has the standard humanoid skeleton (SkeletonProfileHumanoid names, T-pose rest,
# the dataset's proportions), a simple skinned body so importers have something to bind, and
# one animation clip "ikharness" with a key per dataset frame, `1/fps` seconds apart: frame i
# of the dataset is the pose at time i/fps. The dataset's poses were already retargeted by
# Godot's importer when the dataset was built, so any rig that went in comes out here on the
# same rest pose, safe to feed to Unity, an OpenXR application or another Godot project.
extends SceneTree

var opts := {"dataset": "", "out": "", "fps": "2", "scene": "", "animation": "", "no-mesh": false}

func _initialize() -> void:
	quit(run())

func _parse_args() -> void:
	var args := OS.get_cmdline_user_args()
	var i := 0
	while i < args.size():
		var a: String = args[i]
		if a == "--no-mesh":
			opts["no-mesh"] = true
		elif a.begins_with("--") and i + 1 < args.size():
			opts[a.substr(2)] = args[i + 1]
			i += 1
		i += 1

func fail(msg: String) -> int:
	printerr("export_retargeted: ", msg)
	return 1

func run() -> int:
	_parse_args()
	if opts["dataset"] == "" or (opts["out"] == "" and opts["scene"] == "" and opts["animation"] == ""):
		return fail("--dataset and one of --out (glb), --scene (tscn), --animation (res) are required")
	var f := FileAccess.open(opts["dataset"], FileAccess.READ)
	if f == null:
		return fail("cannot read " + String(opts["dataset"]))
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY or doc.get("format", "") != "ikharness-poses/1":
		return fail("not an ikharness-poses/1 dataset")
	var fps := float(opts["fps"])
	if fps <= 0.0:
		return fail("--fps must be positive")

	# The harness's skeleton builder, shared by absolute path (this is a separate project).
	var Rig = load(ProjectSettings.globalize_path("res://").path_join("../harness/rig.gd"))
	if Rig == null:
		return fail("cannot load godot/harness/rig.gd")

	var root := Node3D.new()
	root.name = "Avatar"
	get_root().add_child(root)
	var skeleton: Skeleton3D = Rig.build_skeleton(doc["skeleton"])[0]
	root.add_child(skeleton)
	skeleton.owner = root
	if not opts["no-mesh"]:
		for mi in Rig.build_body(skeleton, Color(0.8, 0.8, 0.8), 1, 1):
			mi.owner = root

	var anim := build_animation(doc, skeleton, fps)
	var library := AnimationLibrary.new()
	library.add_animation(&"ikharness", anim)
	var player := AnimationPlayer.new()
	player.name = "AnimationPlayer"
	root.add_child(player)
	player.owner = root
	player.add_animation_library(&"", library)

	if opts["animation"] != "":
		var err := ResourceSaver.save(anim, opts["animation"])
		if err != OK:
			return fail("saving %s: %s" % [opts["animation"], error_string(err)])
		print("export_retargeted: wrote ", opts["animation"])
	if opts["scene"] != "":
		var packed := PackedScene.new()
		packed.pack(root)
		var err := ResourceSaver.save(packed, opts["scene"])
		if err != OK:
			return fail("saving %s: %s" % [opts["scene"], error_string(err)])
		print("export_retargeted: wrote ", opts["scene"])
	if opts["out"] != "":
		if opts["no-mesh"]:
			# glTF carries a skeleton as the skin of a mesh; without one importers see loose nodes.
			for mi in Rig.build_body(skeleton, Color(0.8, 0.8, 0.8), 1, 1):
				mi.owner = root
		var gltf := GLTFDocument.new()
		var state := GLTFState.new()
		var err := gltf.append_from_scene(root, state)
		if err == OK:
			err = gltf.write_to_filesystem(state, opts["out"])
		if err != OK:
			return fail("writing %s: %s" % [opts["out"], error_string(err)])
		print("export_retargeted: wrote ", opts["out"])
	print("export_retargeted: %d frames at %s fps (%.2f s), %d bones" % [doc["frames"].size(), opts["fps"], anim.length, doc["skeleton"]["bones"].size()])
	return 0

static func xform_of(d: Dictionary) -> Transform3D:
	var p: Array = d["position"]
	var r: Array = d["rotation"]
	return Transform3D(Basis(Quaternion(r[0], r[1], r[2], r[3]).normalized()), Vector3(p[0], p[1], p[2]))

# One rotation track per bone (local to its humanoid parent) and a position track for the hips.
func build_animation(doc: Dictionary, skeleton: Skeleton3D, fps: float) -> Animation:
	var anim := Animation.new()
	var frames: Array = doc["frames"]
	anim.length = maxf(frames.size() - 1, 1) / fps
	anim.loop_mode = Animation.LOOP_NONE
	var path := String(skeleton.name) + ":"
	var parent_of := {}
	for b in doc["skeleton"]["bones"]:
		parent_of[b["name"]] = b.get("parent", "")
	var rot_track := {}
	for name in parent_of:
		var t := anim.add_track(Animation.TYPE_ROTATION_3D)
		anim.track_set_path(t, NodePath(path + name))
		anim.track_set_interpolation_type(t, Animation.INTERPOLATION_LINEAR)
		rot_track[name] = t
	var hips_track := anim.add_track(Animation.TYPE_POSITION_3D)
	anim.track_set_path(hips_track, NodePath(path + "Hips"))
	for i in range(frames.size()):
		var time := i / fps
		var bones: Dictionary = frames[i]["bones"]
		for name in parent_of:
			if not bones.has(name):
				continue
			var g := xform_of(bones[name])
			var parent: String = parent_of[name]
			var local := g if parent == "" or not bones.has(parent) else xform_of(bones[parent]).affine_inverse() * g
			anim.rotation_track_insert_key(rot_track[name], time, local.basis.get_rotation_quaternion())
			if name == "Hips":
				anim.position_track_insert_key(hips_track, time, local.origin)
	return anim
