# Dumps Godot's SkeletonProfileHumanoid (bone names, parents, reference rest
# transforms, tails, groups) to JSON so other languages share the convention.
# Usage: godot --headless --path godot/tools -s dump_humanoid_profile.gd -- out.json
extends SceneTree

func _init():
	var args := OS.get_cmdline_user_args()
	var out_path: String = args[0] if args.size() > 0 else "humanoid_profile.json"
	var sp := SkeletonProfileHumanoid.new()
	var bones: Array = []
	# Accumulate global reference transforms to expose T-pose joint positions.
	var global_by_name: Dictionary = {}
	for b in range(sp.bone_size):
		var name := String(sp.get_bone_name(b))
		var parent := String(sp.get_bone_parent(b))
		var t: Transform3D = sp.get_reference_pose(b)
		var g: Transform3D = t
		if parent != "" and global_by_name.has(parent):
			g = global_by_name[parent] * t
		global_by_name[name] = g
		var q: Quaternion = t.basis.get_rotation_quaternion()
		var gq: Quaternion = g.basis.get_rotation_quaternion()
		bones.append({
			"name": name,
			"parent": parent,
			"group": String(sp.get_group(b)),
			"tail_direction": sp.get_tail_direction(b),
			"bone_tail": String(sp.get_bone_tail(b)),
			"reference_pose": {
				"position": [t.origin.x, t.origin.y, t.origin.z],
				"rotation": [q.x, q.y, q.z, q.w],
			},
			"global_reference_pose": {
				"position": [g.origin.x, g.origin.y, g.origin.z],
				"rotation": [gq.x, gq.y, gq.z, gq.w],
			},
		})
	var doc := {
		"format": "ikharness-humanoid-profile/1",
		"godot_version": Engine.get_version_info()["string"],
		"root_bone": String(sp.root_bone),
		"scale_base_bone": String(sp.scale_base_bone),
		"bones": bones,
	}
	var f := FileAccess.open(out_path, FileAccess.WRITE)
	f.store_string(JSON.stringify(doc, "  "))
	f.close()
	print("wrote ", out_path, " with ", bones.size(), " bones")
	quit()
