# Samples frames of an animation on a humanoid model and writes the global
# (skeleton space) transform of every humanoid bone to a JSON dataset.
#
# Usage:
#   godot --headless --path godot/tools -s export_poses.gd -- \
#       --model /abs/model.glb --anim /abs/clip.tres[:animation_name] \
#       --out /abs/out.json [--frames 40] [--start 0.02] [--end 0.98] \
#       [--hips-mode absolute|normalized|scale:<f>] [--keep-root]
#
# --model   glTF/GLB/FBX whose Skeleton3D uses Godot humanoid bone names in the
#           SkeletonProfileHumanoid T-pose convention (+Y up, faces +Z).
# --anim    a Godot Animation .tres/.res, or a glTF/GLB with an AnimationPlayer
#           (":name" selects the clip, default: first non-RESET clip). Track
#           paths must end in ":<HumanoidBoneName>".
# --hips-mode
#           how Hips position keys map onto the model: "absolute" uses them as
#           is, "normalized" multiplies by the model's Hips rest height (Godot's
#           motion_scale convention), "scale:f" multiplies by f, "ratio:h"
#           multiplies by model_hips_height / h for keys authored on a rig
#           whose standing hips height is h.
extends SceneTree

const FORMAT := "ikharness-poses/1"

var opts := {
	"model": "", "anim": "", "out": "", "frames": 40, "start": 0.02, "end": 0.98,
	"hips-mode": "absolute", "keep-root": false,
}

func _init():
	_parse_args()
	var code := run()
	quit(code)

func _parse_args() -> void:
	var args := OS.get_cmdline_user_args()
	var i := 0
	while i < args.size():
		var a: String = args[i]
		if a.begins_with("--"):
			var key := a.substr(2)
			if key == "keep-root":
				opts[key] = true
			elif i + 1 < args.size():
				var v: String = args[i + 1]
				if key == "frames":
					opts[key] = int(v)
				elif key in ["start", "end"]:
					opts[key] = float(v)
				else:
					opts[key] = v
				i += 1
		i += 1

func fail(msg: String) -> int:
	printerr("export_poses: ", msg)
	return 1

func load_model(path: String) -> Node:
	var root: Node = null
	if path.begins_with("res://"):
		# Imported by a Godot project (retargeted through the importer); run with --path <project>.
		var packed := load(path)
		if packed is PackedScene:
			root = packed.instantiate()
		return root
	if path.to_lower().ends_with(".fbx"):
		var fd := FBXDocument.new()
		var fs := FBXState.new()
		if fd.append_from_file(path, fs) != OK:
			return null
		root = fd.generate_scene(fs)
	else:
		var gd := GLTFDocument.new()
		var gs := GLTFState.new()
		if gd.append_from_file(path, gs) != OK:
			return null
		root = gd.generate_scene(gs)
	return root

func find_skeleton(root: Node) -> Skeleton3D:
	var skels: Array[Node] = root.find_children("*", "Skeleton3D", true, false)
	var best: Skeleton3D = null
	for s in skels:
		if best == null or (s as Skeleton3D).get_bone_count() > best.get_bone_count():
			best = s as Skeleton3D
	return best

func load_animation(spec: String) -> Dictionary:
	# Returns {"anim": Animation, "name": String, "owner": Node or null}
	var path := spec
	var clip := ""
	var colon := spec.rfind(":")
	if colon > 1 and not spec.substr(colon + 1).contains("/"):
		# "file.glb:clip" (but not "C:/...")
		path = spec.substr(0, colon)
		clip = spec.substr(colon + 1)
	var lower := path.to_lower()
	if path.begins_with("res://") and not (lower.ends_with(".tres") or lower.ends_with(".res")):
		var scene := load_model(path)
		if scene == null:
			return {}
		return _first_animation(scene, clip)
	if lower.ends_with(".tres") or lower.ends_with(".res"):
		var res := ResourceLoader.load(path)
		if res is Animation:
			return {"anim": res, "name": path.get_file(), "owner": null}
		return {}
	var scene := load_model(path)
	if scene == null:
		return {}
	return _first_animation(scene, clip)

func _first_animation(scene: Node, clip: String) -> Dictionary:
	var players: Array[Node] = scene.find_children("*", "AnimationPlayer", true, false)
	for p in players:
		var ap := p as AnimationPlayer
		for lib in ap.get_animation_library_list():
			var l := ap.get_animation_library(lib)
			for an in l.get_animation_list():
				if clip != "" and String(an) != clip:
					continue
				var a := l.get_animation(an)
				if clip == "" and (String(an) == "RESET" or a.length < 0.1):
					continue
				return {"anim": a, "name": String(an), "owner": scene}
	scene.free()
	return {}

# Forward kinematics from the bone pose values. Skeleton3D only refreshes its
# cached global poses during a processing frame, which a tool script does not
# have, so the chain is evaluated here: global(b) = global(parent) * pose(b).
func compute_global_poses(skel: Skeleton3D) -> Array[Transform3D]:
	var count := skel.get_bone_count()
	var out: Array[Transform3D] = []
	out.resize(count)
	var done: Array[bool] = []
	done.resize(count)
	for b in range(count):
		_fk_bone(skel, b, out, done)
	return out

func _fk_bone(skel: Skeleton3D, b: int, out: Array[Transform3D], done: Array[bool]) -> Transform3D:
	if done[b]:
		return out[b]
	var local := Transform3D(Basis(skel.get_bone_pose_rotation(b)).scaled(skel.get_bone_pose_scale(b)), skel.get_bone_pose_position(b))
	var parent := skel.get_bone_parent(b)
	var g := local if parent < 0 else _fk_bone(skel, parent, out, done) * local
	out[b] = g
	done[b] = true
	return g

func v3(v: Vector3) -> Array:
	return [v.x, v.y, v.z]

func q4(q: Quaternion) -> Array:
	return [q.x, q.y, q.z, q.w]

func xform(t: Transform3D) -> Dictionary:
	return {"position": v3(t.origin), "rotation": q4(t.basis.get_rotation_quaternion().normalized())}

func run() -> int:
	if opts["model"] == "" or opts["anim"] == "" or opts["out"] == "":
		return fail("--model, --anim and --out are required")

	var model := load_model(opts["model"])
	if model == null:
		return fail("could not load model " + opts["model"])
	var skel := find_skeleton(model)
	if skel == null:
		return fail("model has no Skeleton3D")

	var animd := load_animation(opts["anim"])
	if animd.is_empty():
		return fail("could not load animation " + opts["anim"])
	var anim: Animation = animd["anim"]

	# Humanoid bones present in the model, with nearest humanoid ancestor as parent.
	var profile := SkeletonProfileHumanoid.new()
	var humanoid_names := {}
	for b in range(profile.bone_size):
		humanoid_names[String(profile.get_bone_name(b))] = true
	var bone_ids: Array[int] = []
	var bones_out: Array = []
	for b in range(skel.get_bone_count()):
		var name := skel.get_bone_name(b)
		if not humanoid_names.has(name):
			continue
		bone_ids.append(b)
		var parent := skel.get_bone_parent(b)
		while parent >= 0 and not humanoid_names.has(skel.get_bone_name(parent)):
			parent = skel.get_bone_parent(parent)
		bones_out.append({
			"name": name,
			"parent": skel.get_bone_name(parent) if parent >= 0 else "",
			"rest_local": xform(skel.get_bone_rest(b)),
			"rest_global": xform(skel.get_bone_global_rest(b)),
		})
	if bone_ids.is_empty():
		return fail("model skeleton has no Godot humanoid bone names")

	var hips := skel.find_bone("Hips")
	var head := skel.find_bone("Head")
	if hips < 0 or head < 0:
		return fail("model needs Hips and Head bones")
	var hips_height: float = skel.get_bone_global_rest(hips).origin.y
	var head_height: float = skel.get_bone_global_rest(head).origin.y
	var eye_height: float = head_height
	var leye := skel.find_bone("LeftEye")
	var reye := skel.find_bone("RightEye")
	if leye >= 0 and reye >= 0:
		eye_height = 0.5 * (skel.get_bone_global_rest(leye).origin.y + skel.get_bone_global_rest(reye).origin.y)

	var hips_scale := 1.0
	var mode: String = opts["hips-mode"]
	if mode == "normalized":
		hips_scale = hips_height
	elif mode.begins_with("scale:"):
		hips_scale = float(mode.substr(6))
	elif mode.begins_with("ratio:"):
		# Position keys were authored for a rig whose standing hips height is the given value.
		hips_scale = hips_height / float(mode.substr(6))
	elif mode != "absolute":
		return fail("unknown --hips-mode " + mode)

	# Map animation tracks to bones.
	var tracks: Array = []  # [track index, bone index, type]
	var unmapped := {}
	for t in range(anim.get_track_count()):
		var ty := anim.track_get_type(t)
		if ty != Animation.TYPE_ROTATION_3D and ty != Animation.TYPE_POSITION_3D and ty != Animation.TYPE_SCALE_3D:
			continue
		var np := anim.track_get_path(t)
		if np.get_subname_count() < 1:
			continue
		var bname := String(np.get_subname(np.get_subname_count() - 1))
		var b := skel.find_bone(bname)
		if b < 0:
			unmapped[bname] = true
			continue
		if bname == "Root" and not opts["keep-root"]:
			continue
		tracks.append([t, b, ty])
	if not unmapped.is_empty():
		printerr("export_poses: animation bones not in model (ignored): ", unmapped.keys())

	var n: int = max(1, int(opts["frames"]))
	var t0: float = clampf(float(opts["start"]), 0.0, 1.0) * anim.length
	var t1: float = clampf(float(opts["end"]), 0.0, 1.0) * anim.length
	var frames: Array = []
	for k in range(n):
		var time := t0 if n == 1 else t0 + (t1 - t0) * float(k) / float(n - 1)
		skel.reset_bone_poses()
		for tr in tracks:
			var ti: int = tr[0]
			var b: int = tr[1]
			match tr[2]:
				Animation.TYPE_ROTATION_3D:
					skel.set_bone_pose_rotation(b, anim.rotation_track_interpolate(ti, time))
				Animation.TYPE_POSITION_3D:
					var p := anim.position_track_interpolate(ti, time)
					if b == hips:
						p *= hips_scale
					skel.set_bone_pose_position(b, p)
				Animation.TYPE_SCALE_3D:
					skel.set_bone_pose_scale(b, anim.scale_track_interpolate(ti, time))
		var globals := compute_global_poses(skel)
		var bones := {}
		for b in bone_ids:
			bones[skel.get_bone_name(b)] = xform(globals[b])
		frames.append({"source": 0, "time": time, "bones": bones})

	var doc := {
		"format": FORMAT,
		"generator": {"tool": "godot/tools/export_poses.gd", "godot": Engine.get_version_info()["string"]},
		"skeleton": {
			"convention": "godot-humanoid",
			"source_model": String(opts["model"]).get_file(),
			"hips_height": hips_height,
			"head_height": head_height,
			"eye_height": eye_height,
			"bones": bones_out,
		},
		"sources": [{
			"id": 0,
			"path": String(opts["anim"]),
			"animation": animd["name"],
			"length": anim.length,
			"hips_mode": mode,
			"hips_scale": hips_scale,
			"frame_count": n,
		}],
		"frames": frames,
	}
	var f := FileAccess.open(opts["out"], FileAccess.WRITE)
	if f == null:
		return fail("cannot write " + opts["out"])
	f.store_string(JSON.stringify(doc))
	f.close()
	print("export_poses: wrote %d frames x %d bones from '%s' (%.1fs) to %s" % [n, bone_ids.size(), animd["name"], anim.length, opts["out"]])
	if animd["owner"] != null:
		animd["owner"].free()
	model.free()
	return 0
