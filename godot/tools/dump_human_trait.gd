# Dumps the Mecanim humanoid tables of godot-humanoid's human_trait.gd (Apache-2.0,
# lox9973 / Lyuma) to JSON for the Python ShaderMotion pose layer.
# Usage: godot --headless --path godot/tools -s dump_human_trait.gd -- /abs/human_trait.gd out.json
extends SceneTree

func q(v: Quaternion) -> Array: return [v.x, v.y, v.z, v.w]
func v3(v: Vector3) -> Array: return [v.x, v.y, v.z]

func _init():
	var args := OS.get_cmdline_user_args()
	var ht = load(args[0])
	if ht == null:
		printerr("cannot load ", args[0]); quit(1); return
	var doc := {
		"format": "ikharness-human-trait/1",
		"source": "godot-humanoid human_trait.gd, Copyright 2022-2023 lox9973, 2023-present Lyuma and contributors, Apache-2.0",
		"bone_names": Array(ht.BoneName),
		"godot_names": Array(ht.GodotHumanNames),
		"muscle_names": Array(ht.MuscleName),
		"muscle_from_bone": [], "signs": [], "pre_q": [], "post_q_inverse": [],
		"bone_index_to_mono": [], "bone_index_to_parent": Array(ht.boneIndexToParent),
		"bone_lengths": Array(ht.bone_lengths), "human_bone_mass": Array(ht.human_bone_mass),
		"muscle_default_max": Array(ht.MuscleDefaultMax), "muscle_default_min": Array(ht.MuscleDefaultMin),
	}
	for m in ht.MuscleFromBone: doc["muscle_from_bone"].append(Array(m))
	for s in ht.Signs: doc["signs"].append(v3(s))
	for p in ht.preQ_exported: doc["pre_q"].append(q(p))
	for p in ht.postQ_inverse_exported: doc["post_q_inverse"].append(q(p))
	for b in ht.boneIndexToMono: doc["bone_index_to_mono"].append(int(b))
	var f := FileAccess.open(args[1], FileAccess.WRITE)
	f.store_string(JSON.stringify(doc, " "))
	f.close()
	print("wrote ", args[1], " bones=", doc["bone_names"].size(), " muscles=", doc["muscle_names"].size())
	quit()
