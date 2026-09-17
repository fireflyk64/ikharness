# Prints the scene-relative node path of every Skeleton3D in a runtime-loaded model,
# as needed for the "PATH:..." keys of a .import file's _subresources/nodes block.
# Usage: godot --headless --path godot/tools -s skeleton_path.gd -- /abs/model.glb
extends SceneTree

func _init():
	for path in OS.get_cmdline_user_args():
		var root: Node = null
		if path.to_lower().ends_with(".fbx"):
			var fd := FBXDocument.new(); var fs := FBXState.new()
			if fd.append_from_file(path, fs) == OK: root = fd.generate_scene(fs)
		else:
			var gd := GLTFDocument.new(); var gs := GLTFState.new()
			if gd.append_from_file(path, gs) == OK: root = gd.generate_scene(gs)
		if root == null:
			printerr("skeleton_path: cannot load ", path)
			continue
		for s in root.find_children("*", "Skeleton3D", true, false):
			print("SKELETON ", String(root.get_path_to(s)), " ", (s as Skeleton3D).get_bone_count())
		root.free()
	quit()
