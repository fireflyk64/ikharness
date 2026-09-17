# Renders a calibration pattern through a spatial shader into a SubViewport and saves it,
# to learn how the 3D pipeline transforms colors and what a software-rendered frame costs.
extends SceneTree

var vp: SubViewport
var frames := 0
var out_path := ""

func _init():
	out_path = OS.get_cmdline_user_args()[0]
	vp = SubViewport.new()
	vp.size = Vector2i(256, 32)
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.own_world_3d = true
	vp.transparent_bg = false
	vp.msaa_3d = Viewport.MSAA_DISABLED
	get_root().add_child(vp)
	var cam := Camera3D.new()
	vp.add_child(cam)
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color.BLACK
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	cam.environment = env
	var quad := MeshInstance3D.new()
	var qm := QuadMesh.new()
	qm.size = Vector2(2, 2)
	quad.mesh = qm
	quad.extra_cull_margin = 16384.0
	var sh := Shader.new()
	sh.code = """
shader_type spatial;
render_mode unshaded, cull_disabled, depth_test_disabled, skip_vertex_transform;
uniform bool srgb_to_linear_in_shader = true;
varying vec2 uv;
void vertex() {
	uv = UV;
	POSITION = vec4(VERTEX.xy, 0.5, 1.0);
}
vec3 to_linear(vec3 c) {
	return mix(pow((c + 0.055) / 1.055, vec3(2.4)), c / 12.92, lessThan(c, vec3(0.04045)));
}
void fragment() {
	// top half: gray ramp 0..255 across 256 pixels, as intended sRGB byte values
	float v = floor(uv.x * 256.0) / 255.0;
	vec3 c = uv.y < 0.5 ? vec3(v) : vec3(v, 0.5, 1.0 - v);
	ALBEDO = srgb_to_linear_in_shader ? to_linear(c) : c;
}
"""
	var mat := ShaderMaterial.new()
	mat.shader = sh
	mat.set_shader_parameter("srgb_to_linear_in_shader", OS.get_cmdline_user_args().size() < 2 or OS.get_cmdline_user_args()[1] != "raw")
	quad.material_override = mat
	vp.add_child(quad)
	var n := Node.new()
	n.set_script(Ticker)
	n.tree = self
	get_root().add_child(n)

func tick():
	frames += 1
	if frames == 4:
		var img := vp.get_texture().get_image()
		img.save_png(out_path)
		print("probe: saved ", out_path, " ", img.get_size(), " renderer=", RenderingServer.get_video_adapter_name(), " api=", RenderingServer.get_video_adapter_api_version())
		quit()

class Ticker extends Node:
	var tree
	func _process(_d): tree.tick()
