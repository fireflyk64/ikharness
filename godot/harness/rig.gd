# Helpers shared by the harness and the XR demo: the reference skeleton from a dataset /
# test-file skeleton block, and a simple skinned body so the rig is visible.
extends RefCounted

static func xform_of(d: Dictionary) -> Transform3D:
	var p: Array = d["position"]
	var r: Array = d["rotation"]
	return Transform3D(Basis(Quaternion(r[0], r[1], r[2], r[3]).normalized()), Vector3(p[0], p[1], p[2]))

# Skeleton3D with the block's humanoid bones. A Root bone is inserted above Hips when the
# data has none, because most solvers expect Hips to have a parent. Returns
# [skeleton, bone ids of the block's bones in file order].
static func build_skeleton(sk: Dictionary) -> Array:
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
	var ids: Array[int] = []
	for b in bones:
		var idx := skel.find_bone(b["name"])
		var parent: String = b.get("parent", "")
		var pidx := skel.find_bone(parent) if parent != "" else (skel.find_bone("Root") if not has_root else -1)
		skel.set_bone_parent(idx, pidx)
		skel.set_bone_rest(idx, xform_of(b["rest_local"]))
		ids.append(idx)
	skel.reset_bone_poses()
	return [skel, ids]

const LEAF_LENGTH := {"Head": 0.2, "LeftHand": 0.09, "RightHand": 0.09, "LeftToes": 0.07, "RightToes": 0.07, "LeftFoot": 0.12, "RightFoot": 0.12}
const HEAD_BONES := ["Head", "LeftEye", "RightEye", "Jaw"]

static func _segment(st: SurfaceTool, bone: int, a: Vector3, b: Vector3, radius: float) -> void:
	var axis := b - a
	var length := axis.length()
	if length < 1e-4:
		return
	var dir := axis / length
	var side := dir.cross(Vector3.UP if absf(dir.y) < 0.9 else Vector3.RIGHT).normalized()
	var up := dir.cross(side)
	var mid := a + axis * 0.25
	var ring := [mid + side * radius, mid + up * radius, mid - side * radius, mid - up * radius]
	for i in range(4):
		var r0: Vector3 = ring[i]
		var r1: Vector3 = ring[(i + 1) % 4]
		for tri in [[a, r1, r0], [b, r0, r1]]:
			for v in tri:
				st.set_bones(PackedInt32Array([bone, 0, 0, 0]))
				st.set_weights(PackedFloat32Array([1.0, 0.0, 0.0, 0.0]))
				st.add_vertex(v)

# Octahedral "bone" shapes skinned to the skeleton: one MeshInstance3D for the body on
# `body_layer` and one for the head on `head_layer`, so a first-person camera can leave the
# head out. Returns [body, head].
static func build_body(skeleton: Skeleton3D, color: Color, body_layer: int = 1, head_layer: int = 2) -> Array:
	var tools := [SurfaceTool.new(), SurfaceTool.new()]
	for st in tools:
		st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var has_child := {}
	for b in range(skeleton.get_bone_count()):
		var p := skeleton.get_bone_parent(b)
		if p < 0 or skeleton.get_bone_parent(p) < 0 and skeleton.get_bone_name(p) == "Root":
			continue
		has_child[p] = true
		var pa := skeleton.get_bone_global_rest(p).origin
		var pb := skeleton.get_bone_global_rest(b).origin
		var which := 1 if skeleton.get_bone_name(p) in HEAD_BONES else 0
		_segment(tools[which], p, pa, pb, clampf(pa.distance_to(pb) * 0.18, 0.006, 0.05))
	for b in range(skeleton.get_bone_count()):
		var name := skeleton.get_bone_name(b)
		if has_child.has(b) and name != "Head" or name == "Root":
			continue
		var g := skeleton.get_bone_global_rest(b)
		var length: float = LEAF_LENGTH.get(name, 0.025)
		var which := 1 if name in HEAD_BONES else 0
		_segment(tools[which], b, g.origin, g.origin + g.basis.y.normalized() * length, length * (0.45 if name == "Head" else 0.25))
	var out := []
	var skin := skeleton.create_skin_from_rest_transforms()
	for i in range(2):
		var st: SurfaceTool = tools[i]
		st.generate_normals()
		var mat := StandardMaterial3D.new()
		mat.albedo_color = color
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		var mi := MeshInstance3D.new()
		mi.name = "Body" if i == 0 else "HeadShape"
		mi.mesh = st.commit()
		if mi.mesh.get_surface_count() > 0:
			mi.mesh.surface_set_material(0, mat)
		mi.skin = skin
		mi.layers = body_layer if i == 0 else head_layer
		mi.extra_cull_margin = 4.0
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		skeleton.add_child(mi)
		mi.skeleton = mi.get_path_to(skeleton)
		out.append(mi)
	return out
