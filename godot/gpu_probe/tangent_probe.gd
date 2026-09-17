# What do NORMAL / TANGENT / BINORMAL look like in vertex() for a skinned mesh?
extends SceneTree
var vp: SubViewport
var skel: Skeleton3D
var frames := 0
var out_path := ""
var rotate := false

func _init():
	out_path = OS.get_cmdline_user_args()[0]
	rotate = OS.get_cmdline_user_args().size() > 1
	vp = SubViewport.new(); vp.size = Vector2i(96, 32); vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	get_root().add_child(vp)
	var cam := Camera3D.new(); vp.add_child(cam)
	skel = Skeleton3D.new(); skel.add_bone("Root"); skel.add_bone("B"); skel.set_bone_parent(1, 0)
	skel.set_bone_rest(1, Transform3D(Basis(), Vector3(0, 1, 0))); skel.reset_bone_poses()
	vp.add_child(skel)
	var arrays := []; arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = PackedVector3Array([Vector3(0,1,0), Vector3(0,1,0), Vector3(0,1,0)])
	arrays[Mesh.ARRAY_NORMAL] = PackedVector3Array([Vector3(1,0,0), Vector3(1,0,0), Vector3(1,0,0)])
	arrays[Mesh.ARRAY_TANGENT] = PackedFloat32Array([0,1,0,1, 0,1,0,1, 0,1,0,1])
	arrays[Mesh.ARRAY_TEX_UV] = PackedVector2Array([Vector2(0,0), Vector2(1,0), Vector2(0,1)])
	arrays[Mesh.ARRAY_BONES] = PackedInt32Array([1,0,0,0, 1,0,0,0, 1,0,0,0])
	arrays[Mesh.ARRAY_WEIGHTS] = PackedFloat32Array([1,0,0,0, 1,0,0,0, 1,0,0,0])
	var mesh := ArrayMesh.new(); mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	mesh.custom_aabb = AABB(Vector3(-100,-100,-100), Vector3(200,200,200))
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode unshaded, cull_disabled, depth_test_disabled, skip_vertex_transform;
varying vec3 n; varying vec3 t; varying vec3 b; varying vec2 r;
void vertex() {
	n = NORMAL; t = TANGENT; b = BINORMAL;
	vec2 c = UV.x > 0.5 ? vec2(4.0, -1.0) : (UV.y > 0.5 ? vec2(-1.0, 4.0) : vec2(-1.0, -1.0));
	r = c;
	POSITION = vec4(c.x * 2.0 - 1.0, c.y * 2.0 - 1.0, 0.5, 1.0);
}
void fragment() {
	vec3 v = r.x < 0.3333 ? n : (r.x < 0.6666 ? t : b);
	ALBEDO = v * 0.5 + 0.5;
}
"""
	var mat := ShaderMaterial.new(); mat.shader = sh; mesh.surface_set_material(0, mat)
	var mi := MeshInstance3D.new(); mi.mesh = mesh; mi.skin = skel.create_skin_from_rest_transforms(); mi.extra_cull_margin = 16384.0
	skel.add_child(mi); mi.skeleton = mi.get_path_to(skel)
	var n := Node.new(); n.set_script(Ticker); n.tree = self; get_root().add_child(n)

func tick():
	frames += 1
	if frames == 2 and rotate:
		# 90 degrees about Z: x -> y, y -> -x, z -> z
		skel.set_bone_pose_rotation(1, Quaternion(Vector3(0, 0, 1), PI / 2.0))
	if frames == 6:
		vp.get_texture().get_image().save_png(out_path)
		quit()

class Ticker extends Node:
	var tree
	func _process(_d): tree.tick()
