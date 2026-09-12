# Loads a glTF/GLB (or FBX) at runtime and prints its skeletons and animations.
# Usage: godot --headless --path godot/tools -s inspect_model.gd -- /abs/path/model.glb
extends SceneTree

func _init():
	var args := OS.get_cmdline_user_args()
	for path in args:
		inspect(path)
	quit()

func inspect(path: String) -> void:
	print("== ", path)
	var root: Node = null
	if path.ends_with(".fbx"):
		var fd := FBXDocument.new()
		var fs := FBXState.new()
		var err := fd.append_from_file(path, fs)
		if err != OK:
			print("   FBX load error ", err); return
		root = fd.generate_scene(fs)
	else:
		var gd := GLTFDocument.new()
		var gs := GLTFState.new()
		var err := gd.append_from_file(path, gs)
		if err != OK:
			print("   GLTF load error ", err); return
		root = gd.generate_scene(gs)
	if root == null:
		print("   no scene"); return
	var skels: Array[Node] = root.find_children("*", "Skeleton3D", true, false)
	for s in skels:
		var skel := s as Skeleton3D
		print("   Skeleton3D '", skel.name, "' bones=", skel.get_bone_count(), " unique_name=", skel.unique_name_in_owner, " motion_scale=", skel.motion_scale)
		var names: Array = []
		for b in range(skel.get_bone_count()):
			names.append(skel.get_bone_name(b))
		print("   bones: ", names)
		for bn in ["Hips", "Head", "LeftHand", "LeftFoot", "LeftEye"]:
			var b := skel.find_bone(bn)
			if b >= 0:
				var g := skel.get_bone_global_rest(b)
				var l := skel.get_bone_rest(b)
				print("   rest ", bn, " global_pos=", g.origin, " global_rot=", g.basis.get_rotation_quaternion(), " local_pos=", l.origin, " local_rot=", l.basis.get_rotation_quaternion())
	var players: Array[Node] = root.find_children("*", "AnimationPlayer", true, false)
	for p in players:
		var ap := p as AnimationPlayer
		for lib in ap.get_animation_library_list():
			for an in ap.get_animation_library(lib).get_animation_list():
				var a := ap.get_animation_library(lib).get_animation(an)
				var types := {}
				var first_path := ""
				for t in range(a.get_track_count()):
					var ty := a.track_get_type(t)
					types[ty] = types.get(ty, 0) + 1
					if first_path == "": first_path = String(a.track_get_path(t))
				print("   anim '", an, "' length=", a.length, " step=", a.step, " tracks=", a.get_track_count(), " types=", types, " first_path=", first_path)
	root.free()
