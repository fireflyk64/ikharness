# GPU ShaderMotion encoder: a skinned "recorder" mesh plus a spatial shader, the way
# ShaderMotion works inside closed applications where only meshes and shaders exist.
#
# One triangle per bone, using only skinned NORMALs and positions (skinned TANGENTs arrive
# garbled in the Compatibility renderer, see godot/gpu_probe/tangent_probe.gd):
#   A: bone,   normal +X, position = joint            -> R_d(bone) x, posed joint
#   C: bone,   normal +Y                              -> R_d(bone) y
#   B: parent, normal +X, position = joint + Y        -> R_d(parent) x, posed joint + R_d(parent) y
# where R_d = pose * rest^-1 is the rest-relative rotation skinning applies. The parent's
# second axis is B.position - A.position (the joint is rigidly attached to the parent).
# Godot has no geometry shaders, so every vertex multiplies its data by a role flag and the
# fragment shader divides by / normalizes away the interpolated barycentric weight, which
# makes both bones' rotations available per fragment. Per-bone constants ride in CUSTOM0..3 (identical on
# the three vertices, so interpolation leaves them untouched):
#   CUSTOM0 = L = preQ^-1 * G_parent^-1      CUSTOM1 = R = G_bone * postQ
#   CUSTOM2 = (sign.xyz incl. (1,-1,-1) and 0 for locked axes, kind)   kind 1 = hips
#   CUSTOM3 = (first slot, channel of slot 0, 1, 2; -1 = none)
# so that swing = L * (R_d_parent^-1 * R_d_bone) * R, exactly the CPU encoder's
# preQ^-1 * local * postQ. No twist leftovers are passed between bones (a shader cannot).
extends RefCounted

const HumanTrait := preload("res://addons/humanoid/human_trait.gd")
const Encoder := preload("res://shadermotion_encoder.gd")

const RENDER_LAYER := 1 << 19

static func _q(q: Quaternion) -> Array:
	return [q.x, q.y, q.z, q.w]

static func build_recorder(skeleton: Skeleton3D, hips_height: float) -> MeshInstance3D:
	var table := Encoder.bone_slots()
	var index_of := {}
	for i in range(HumanTrait.GodotHumanNames.size()):
		index_of[HumanTrait.GodotHumanNames[i]] = i

	var verts := PackedVector3Array()
	var normals := PackedVector3Array()
	var uvs := PackedVector2Array()
	var bones := PackedInt32Array()
	var weights := PackedFloat32Array()
	var c0 := PackedFloat32Array()
	var c1 := PackedFloat32Array()
	var c2 := PackedFloat32Array()
	var c3 := PackedFloat32Array()

	for b in range(skeleton.get_bone_count()):
		var name := skeleton.get_bone_name(b)
		var is_hips := name == "Hips"
		if not is_hips and (not table.has(name) or not index_of.has(name)):
			continue
		var parent := skeleton.get_bone_parent(b)
		if parent < 0:
			continue
		var g_b := skeleton.get_bone_global_rest(b).basis.get_rotation_quaternion()
		var g_p := skeleton.get_bone_global_rest(parent).basis.get_rotation_quaternion()
		var k0: Array; var k1: Array; var k2: Array; var k3: Array
		if is_hips:
			k0 = _q(g_b)
			k1 = [0.0, 0.0, 0.0, 1.0]
			k2 = [hips_height, 0.0, 0.0, 1.0]
			k3 = [0.0, -1.0, -1.0, -1.0]
		else:
			var idx: int = index_of[name]
			var pre: Quaternion = HumanTrait.preQ_exported[idx]
			var inv_post: Quaternion = HumanTrait.postQ_inverse_exported[idx]
			var mfb: PackedInt32Array = HumanTrait.MuscleFromBone[idx]
			var sgn: Vector3 = Vector3(1, -1, -1) * HumanTrait.Signs[idx]
			for i in range(3):
				if mfb[i] == -1:
					sgn[i] = 0.0
			k0 = _q((pre.inverse() * g_p.inverse()).normalized())
			k1 = _q((g_b * inv_post.inverse()).normalized())
			k2 = [sgn.x, sgn.y, sgn.z, 0.0]
			var entry: Array = table[name]
			var ch: Array = entry[1]
			k3 = [float(entry[0]), float(ch[0]), float(ch[1]) if ch.size() > 1 else -1.0, float(ch[2]) if ch.size() > 2 else -1.0]
		var origin := skeleton.get_bone_global_rest(b).origin
		# [rest position, bone, role uv, normal]
		var corner := [
			[origin, b, Vector2(1, 0), Vector3(1, 0, 0)],
			[origin + Vector3(0, 1, 0), parent, Vector2(0, 1), Vector3(1, 0, 0)],
			[origin, b, Vector2(0, 0), Vector3(0, 1, 0)],
		]
		for c in corner:
			verts.append(c[0])
			normals.append(c[3])
			uvs.append(c[2])
			bones.append_array([c[1], 0, 0, 0])
			weights.append_array([1.0, 0.0, 0.0, 0.0])
			c0.append_array(k0); c1.append_array(k1); c2.append_array(k2); c3.append_array(k3)

	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_BONES] = bones
	arrays[Mesh.ARRAY_WEIGHTS] = weights
	arrays[Mesh.ARRAY_CUSTOM0] = c0
	arrays[Mesh.ARRAY_CUSTOM1] = c1
	arrays[Mesh.ARRAY_CUSTOM2] = c2
	arrays[Mesh.ARRAY_CUSTOM3] = c3
	var flags: int = (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT) \
		| (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM1_SHIFT) \
		| (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM2_SHIFT) \
		| (Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM3_SHIFT)
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays, [], {}, flags)
	mesh.custom_aabb = AABB(Vector3(-100, -100, -100), Vector3(200, 200, 200))

	var shader := Shader.new()
	shader.code = shader_code()
	var mat := ShaderMaterial.new()
	mat.shader = shader
	mesh.surface_set_material(0, mat)

	var mi := MeshInstance3D.new()
	mi.name = "ShaderMotionRecorder"
	mi.mesh = mesh
	mi.skin = skeleton.create_skin_from_rest_transforms()
	mi.extra_cull_margin = 16384.0
	mi.layers = RENDER_LAYER
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	skeleton.add_child(mi)
	mi.skeleton = mi.get_path_to(skeleton)
	return mi

static func make_viewport(size: Vector2i) -> SubViewport:
	var vp := SubViewport.new()
	vp.name = "ShaderMotionViewport"
	vp.size = size
	vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	vp.transparent_bg = false
	vp.msaa_3d = Viewport.MSAA_DISABLED
	vp.screen_space_aa = Viewport.SCREEN_SPACE_AA_DISABLED
	vp.use_debanding = false
	var cam := Camera3D.new()
	cam.cull_mask = RENDER_LAYER
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color.BLACK
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	cam.environment = env
	vp.add_child(cam)
	return vp

static func shader_code() -> String:
	return """
shader_type spatial;
render_mode unshaded, cull_disabled, depth_test_disabled, skip_vertex_transform;

const float PI_ = 3.14159265358979;
const float HALF = 364.0;
const int POW_ = 729;

varying vec3 v_bone_x;
varying vec3 v_bone_y;
varying vec3 v_parent_x;
varying vec4 v_pos_a;
varying vec4 v_pos_b;
varying vec4 v_c0;
varying vec4 v_c1;
varying vec4 v_c2;
varying vec4 v_c3;
varying vec2 v_rect;

float slot_count(vec4 c2, vec4 c3) {
	if (c2.w > 0.5) { return 12.0; }
	return 1.0 + step(-0.5, c3.z) + step(-0.5, c3.w);
}

void vertex() {
	float role_a = UV.x;
	float role_b = UV.y;
	float role_c = 1.0 - UV.x - UV.y;
	v_bone_x = NORMAL * role_a;
	v_bone_y = NORMAL * role_c;
	v_parent_x = NORMAL * role_b;
	v_pos_a = vec4(VERTEX * role_a, role_a);
	v_pos_b = vec4(VERTEX * role_b, role_b);
	v_c0 = CUSTOM0; v_c1 = CUSTOM1; v_c2 = CUSTOM2; v_c3 = CUSTOM3;
	// An oversized triangle around the bone's slot rectangle keeps every barycentric
	// weight >= 1/5 inside the rectangle. Rect coordinates: (0,0) top-left .. (1,1).
	vec2 corner = UV.x > 0.5 ? vec2(-1.0, -1.0) : (UV.y > 0.5 ? vec2(4.0, -1.0) : vec2(-1.0, 4.0));
	v_rect = corner;
	float n = slot_count(CUSTOM2, CUSTOM3);
	float base = CUSTOM3.x;
	float col = floor(base / 45.0 + 0.0001);
	float row = base - col * 45.0;
	vec2 rect_min = vec2(col * 2.0 / 80.0, row / 45.0);      // fractions of the frame, y from the top
	vec2 rect_size = vec2(2.0 / 80.0, n / 45.0);
	vec2 frame = rect_min + corner * rect_size;
	// The viewport texture comes back with +Y of clip space at the bottom of the image.
	POSITION = vec4(frame.x * 2.0 - 1.0, frame.y * 2.0 - 1.0, 0.5, 1.0);
}

vec4 qmul(vec4 a, vec4 b) {
	return vec4(a.w * b.xyz + b.w * a.xyz + cross(a.xyz, b.xyz), a.w * b.w - dot(a.xyz, b.xyz));
}

vec4 quat_from_mat(mat3 m) {
	float t = m[0][0] + m[1][1] + m[2][2];
	vec4 q;
	if (t > 0.0) {
		float s = sqrt(t + 1.0) * 2.0;
		q = vec4((m[1][2] - m[2][1]) / s, (m[2][0] - m[0][2]) / s, (m[0][1] - m[1][0]) / s, 0.25 * s);
	} else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
		float s = sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0;
		q = vec4(0.25 * s, (m[1][0] + m[0][1]) / s, (m[2][0] + m[0][2]) / s, (m[1][2] - m[2][1]) / s);
	} else if (m[1][1] > m[2][2]) {
		float s = sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0;
		q = vec4((m[1][0] + m[0][1]) / s, 0.25 * s, (m[2][1] + m[1][2]) / s, (m[2][0] - m[0][2]) / s);
	} else {
		float s = sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0;
		q = vec4((m[2][0] + m[0][2]) / s, (m[2][1] + m[1][2]) / s, 0.25 * s, (m[0][1] - m[1][0]) / s);
	}
	return normalize(q);
}

mat3 mat_from_quat(vec4 q) {
	float x = q.x, y = q.y, z = q.z, w = q.w;
	return mat3(
		vec3(1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y + z * w), 2.0 * (x * z - y * w)),
		vec3(2.0 * (x * y - z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z + x * w)),
		vec3(2.0 * (x * z + y * w), 2.0 * (y * z - x * w), 1.0 - 2.0 * (x * x + y * y)));
}

mat3 frame_from(vec3 ax, vec3 ay) {
	vec3 x = normalize(ax);
	vec3 y = normalize(ay - x * dot(x, ay));
	return mat3(x, y, cross(x, y));
}

// Quaternion -> (twist x, swing y, swing z) in radians.
vec3 swing_twist_inv(vec4 q) {
	if (q.w < 0.0) { q = -q; }
	float a = q.x, b = q.y, c = q.z, d = q.w;
	float n = a * a + d * d;
	float tx = n > 1e-12 ? sqrt(a * a / n) : 0.0;
	tx = min(tx, 1.0);
	if (a < 0.0) { tx = -tx; }
	float tw = sqrt(max(0.0, 1.0 - tx * tx));
	float sw = abs(tx) < 1e-6 ? d / tw : a / tx;
	sw = clamp(sw, -1.0, 1.0);
	float x = asin(tx) * 2.0;
	float yz = acos(sw) * 2.0;
	float sinc = abs(yz) < 1e-6 ? 0.5 : sin(yz / 2.0) / yz;
	return vec3(x, (b * tw - c * tx) / sinc, (b * tx + c * tw) / sinc);
}

int gray_digit(int n, int i) {
	// stored digit i (0 = most significant) of the base-3 reflected Gray code of n
	int state = 0;
	int stored = 0;
	int div = 243;
	for (int k = 0; k <= i; k++) {
		int d = (n / div) % 3;
		stored = ((state & 1) == 0) ? d : 2 - d;
		state = state * 3 + d;
		div /= 3;
	}
	return stored;
}

// Value in [-1, 1] -> color number k (0 or 1) of its slot.
vec3 encode_snorm(float value, int k) {
	float t = (clamp(value, -1.0, 1.0) + 1.0) * HALF;
	int lo = int(floor(t));
	float fr = t - float(lo);
	if (lo >= POW_ - 1) { lo = POW_ - 1; fr = 0.0; }
	vec3 ch;
	for (int j = 0; j < 3; j++) {
		int i = k * 3 + j;
		float a = float(gray_digit(lo, i));
		float b = fr > 0.0 ? float(gray_digit(lo + 1, i)) : a;
		ch[j] = (a + (b - a) * fr) * 0.5;
	}
	return vec3(ch[1], ch[0], ch[2]);   // digits arrive in g, r, b order
}

vec2 encode_float(float value) {
	float t = clamp(value * HALF + (729.0 * 729.0 - 1.0) / 2.0, 0.0, 729.0 * 729.0 - 1.0);
	float nf = floor(t + 0.5);
	float f = t - nf;
	int n = int(nf);
	int r = n / POW_;
	int x = n - r * POW_;
	float h = float(r);
	float lf = f;
	if (x == 0 && f < 0.0) { h = float(r) + f; lf = 0.0; }
	else if (x == POW_ - 1 && f > 0.0) { h = float(r) + f; lf = 0.0; }
	float l = float(x) + lf;
	if ((r & 1) != 0) { l = float(POW_ - 1) - l; }
	return vec2((h - HALF) / HALF, (l - HALF) / HALF);
}

void fragment() {
	if (v_rect.x < 0.0 || v_rect.x >= 1.0 || v_rect.y < 0.0 || v_rect.y >= 1.0) { discard; }
	float n = slot_count(v_c2, v_c3);
	int i = int(floor(v_rect.y * n));
	int k = v_rect.x < 0.5 ? 0 : 1;
	mat3 rb = frame_from(v_bone_x, v_bone_y);
	float value = 0.0;
	if (v_c2.w > 0.5) {
		// Hips: world position in meters and the posed basis, mirrored in X for Unity.
		vec3 p = v_pos_a.xyz / v_pos_a.w;
		vec3 pu = vec3(-p.x, p.y, p.z) / 2.0;
		mat3 g = rb * mat_from_quat(v_c0);
		vec3 ry = normalize(vec3(-g[1].x, g[1].y, g[1].z));
		vec3 rz = normalize(vec3(-g[2].x, g[2].y, g[2].z));
		float scale = v_c2.x;
		float ly = scale >= 1.0 ? 1.0 : scale;
		float lz = scale >= 1.0 ? 1.0 / scale : 1.0;
		if (i < 3) { value = encode_float(pu[i]).x; }
		else if (i < 6) { value = encode_float(pu[i - 3]).y; }
		else if (i < 9) { value = ry[i - 6] * ly; }
		else { value = rz[i - 9] * lz; }
	} else {
		vec3 parent_y = v_pos_b.xyz / v_pos_b.w - v_pos_a.xyz / v_pos_a.w;
		mat3 rp = frame_from(v_parent_x, parent_y);
		vec4 qd = quat_from_mat(transpose(rp) * rb);
		vec4 swing = normalize(qmul(qmul(v_c0, qd), v_c1));
		vec3 st = swing_twist_inv(swing) * v_c2.xyz / PI_;
		st = mod(st + 1.0, 2.0) - 1.0;
		int chan = int(floor((i == 0 ? v_c3.y : (i == 1 ? v_c3.z : v_c3.w)) + 0.5));
		value = st[chan];
	}
	ALBEDO = encode_snorm(value, k);
}
"""
