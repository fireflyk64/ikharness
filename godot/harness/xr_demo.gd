# ikharness XR demo: an avatar driven by OpenXR devices, with T-pose calibration and the
# ShaderMotion recorder on screen. It is the "application under test" for the whole chain
#   dataset -> virtual trackers -> Monado driver -> OpenXR -> IK -> shader -> screen pixels.
#
#   godot --path godot/harness --xr-mode on --rendering-driver opengl3 -s xr_demo.gd -- \
#       --skeleton /abs/dataset.json [--ik builtin|renik|none] [--roles head,left_hand,...]
#       [--port 4343] [--window 640x360] [--spectator 1] [--status /abs/status.json] [--quit-after N]
#
# Head, hands and triggers come through OpenXR. Body trackers come from the driver's state
# query (driver_client.gd) because Monado has no XR_HTCX_vive_tracker_interaction.
# Calibration: stand in the avatar's rest pose (T-pose) and pull both triggers.
#
# The desktop window shows a spectator view with the ShaderMotion slots of the avatar's pose
# on its left edge, like a ShaderMotion stream from a closed application. `ikh xr` runs it.
extends SceneTree

const Harness := preload("res://harness.gd")
const Calibration := preload("res://calibration.gd")
const ShaderMotionGPU := preload("res://shadermotion_gpu.gd")
const ShaderMotionEncoder := preload("res://shadermotion_encoder.gd")
const Rig := preload("res://rig.gd")
const DriverClient := preload("res://driver_client.gd")

const BODY_ROLES := ["waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow", "right_elbow"]
const LAYER_WORLD := 1
const LAYER_HEAD := 2
const TRIGGER_PRESSED := 0.75

var opts := {"skeleton": "", "ik": "builtin", "roles": "", "port": 4343, "host": "127.0.0.1", "status": "",
	"window": "640x360", "spectator": 1, "quit-after": 0}

var xr: XRInterface
var world: Node3D
var avatar: Node3D
var skeleton: Skeleton3D
var xr_camera: XRCamera3D
var controllers := {}
var driver: RefCounted
var adapter = null
var roles: Array = []
var calibration := {}
var calibrations := 0
var markers := {}
var spectator_camera: Camera3D
var frames := 0
var focused := false
var triggers_were_down := false
var last_status := ""
var debug := OS.get_environment("IKH_DEBUG") != ""
var debug_last := {}

func _init():
	_parse_args()
	var f := FileAccess.open(opts["skeleton"], FileAccess.READ) if opts["skeleton"] != "" else null
	if f == null:
		printerr("xr_demo: --skeleton <dataset or tracker test file> is required")
		quit(1)
		return
	var doc = JSON.parse_string(f.get_as_text())
	if typeof(doc) != TYPE_DICTIONARY or not doc.has("skeleton"):
		printerr("xr_demo: ", opts["skeleton"], " has no skeleton block")
		quit(1)
		return
	xr = XRServer.find_interface("OpenXR")
	if xr == null or not xr.is_initialized():
		printerr("xr_demo: OpenXR is not running. Start with --xr-mode on and a runtime (XR_RUNTIME_JSON), e.g. through `ikh xr`.")
		quit(3)
		return
	if DisplayServer.get_name() == "headless":
		printerr("xr_demo: needs a renderer; run without --headless (software OpenGL under Xvfb is fine)")
		quit(1)
		return
	xr.session_focussed.connect(func(): focused = true)
	xr.session_visible.connect(func(): focused = false)

	Input.mouse_mode = Input.MOUSE_MODE_HIDDEN
	var size := _window_size()
	var window := get_root()
	window.borderless = true
	window.size = size
	window.position = Vector2i.ZERO

	world = Node3D.new()
	world.name = "World"
	window.add_child(world)
	_build_environment()

	# OpenXR renders into its own viewport so the window is free for the spectator view.
	var xr_viewport := SubViewport.new()
	xr_viewport.name = "XRViewport"
	xr_viewport.use_xr = true
	xr_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	world.add_child(xr_viewport)
	var origin := XROrigin3D.new()
	xr_viewport.add_child(origin)
	xr_camera = XRCamera3D.new()
	xr_camera.cull_mask = LAYER_WORLD
	origin.add_child(xr_camera)
	for hand in ["left_hand", "right_hand"]:
		var c := XRController3D.new()
		c.name = hand
		c.tracker = StringName(hand)
		c.pose = &"grip"
		origin.add_child(c)
		controllers[hand] = c

	avatar = Node3D.new()
	avatar.name = "Avatar"
	world.add_child(avatar)
	skeleton = Rig.build_skeleton(doc["skeleton"])[0]
	avatar.add_child(skeleton)
	Rig.build_body(skeleton, Color(0.85, 0.55, 0.25), LAYER_WORLD, LAYER_HEAD)
	ShaderMotionGPU.build_recorder(skeleton, float(doc["skeleton"]["hips_height"]))

	_build_window(size)

	driver = DriverClient.new()
	driver.open(String(opts["host"]), int(opts["port"]))

	var ticker := Ticker.new()
	ticker.demo = self
	world.add_child(ticker)
	print("xr_demo: started, window %dx%d, %s, eye %s" % [size.x, size.y, RenderingServer.get_video_adapter_name(), xr.get_render_target_size()])

func _parse_args() -> void:
	var args := OS.get_cmdline_user_args()
	var i := 0
	while i < args.size():
		var a: String = args[i]
		if a.begins_with("--") and i + 1 < args.size():
			var key := a.substr(2)
			opts[key] = int(args[i + 1]) if key in ["port", "spectator", "quit-after"] else args[i + 1]
			i += 1
		i += 1

func _window_size() -> Vector2i:
	var parts := String(opts["window"]).split("x")
	if parts.size() != 2 or int(parts[0]) < 80 or int(parts[1]) < 45:
		return Vector2i(640, 360)
	return Vector2i(int(parts[0]), int(parts[1]))

func _build_environment() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.42, 0.55, 0.68)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.7, 0.7, 0.7)
	var we := WorldEnvironment.new()
	we.environment = env
	world.add_child(we)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55, 30, 0)
	world.add_child(sun)
	var img := Image.create(2, 2, false, Image.FORMAT_RGB8)
	for y in range(2):
		for x in range(2):
			img.set_pixel(x, y, Color(0.36, 0.38, 0.36) if (x + y) % 2 == 0 else Color(0.46, 0.48, 0.46))
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = ImageTexture.create_from_image(img)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	mat.uv1_scale = Vector3(8, 8, 1)   # 0.5 m squares on the 8 m floor
	var floor_mesh := PlaneMesh.new()
	floor_mesh.size = Vector2(8, 8)
	var floor_node := MeshInstance3D.new()
	floor_node.name = "Floor"
	floor_node.mesh = floor_mesh
	floor_node.material_override = mat
	world.add_child(floor_node)

# Window = spectator view, with the recorder's slot columns over its left edge. The slots
# are drawn 1:1 from the recorder viewport so their pixels reach the screen unfiltered.
func _build_window(size: Vector2i) -> void:
	var window := get_root()
	if int(opts["spectator"]) != 0:
		var sv := SubViewport.new()
		sv.name = "SpectatorViewport"
		sv.size = size
		sv.render_target_update_mode = SubViewport.UPDATE_ALWAYS
		window.add_child(sv)
		spectator_camera = Camera3D.new()
		spectator_camera.cull_mask = LAYER_WORLD | LAYER_HEAD
		spectator_camera.fov = 50.0
		sv.add_child(spectator_camera)
		var view := TextureRect.new()
		view.name = "Spectator"
		view.texture = sv.get_texture()
		view.size = size
		window.add_child(view)
	else:
		var bg := ColorRect.new()
		bg.color = Color.BLACK
		bg.size = size
		window.add_child(bg)

	var rv := ShaderMotionGPU.make_viewport(size)
	window.add_child(rv)
	var last_slot := 11
	var table := ShaderMotionEncoder.bone_slots()
	for name in table:
		last_slot = maxi(last_slot, int(table[name][0]) + table[name][1].size() - 1)
	var columns := last_slot / 45 + 1
	var clip := Control.new()
	clip.name = "ShaderMotion"
	clip.clip_contents = true
	clip.size = Vector2(ceil(columns * 2.0 / 80.0 * size.x), size.y)
	window.add_child(clip)
	var strip := TextureRect.new()
	strip.texture = rv.get_texture()
	strip.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	strip.size = size
	clip.add_child(strip)

func _place_spectator() -> void:
	if spectator_camera == null:
		return
	var root := avatar.global_transform
	var eye := root * Vector3(-1.1, 1.35, 2.3)
	spectator_camera.look_at_from_position(eye, root * Vector3(0.3, 0.9, 0.0), Vector3.UP)

# Tracker poses in tracking space (= world, the XR origin stays at the world origin).
func tracker_poses() -> Dictionary:
	var out := {}
	var head := XRServer.get_tracker(&"head") as XRPositionalTracker
	if head != null and head.has_pose(&"default") and head.get_pose(&"default").has_tracking_data:
		out["head"] = xr_camera.global_transform
	for hand in controllers:
		var c: XRController3D = controllers[hand]
		if c.get_is_active() and c.get_has_tracking_data():
			out[hand] = c.global_transform
	for role in BODY_ROLES:
		if driver.state.has(role):
			out[role] = driver.state[role]
	return out

func wanted_roles(poses: Dictionary) -> Array:
	if String(opts["roles"]) != "":
		return Array(String(opts["roles"]).split(","))
	return poses.keys()

func has_tracker(role: String) -> bool:
	return roles.has(role)

# Used by the IK adapters: the world-space pose of the bone that `role`'s tracker follows.
func bone_target_xform(frame: Dictionary, role: String) -> Transform3D:
	return frame[role] * calibration["offsets"][role].affine_inverse()

func calibrate(poses: Dictionary) -> bool:
	var want := wanted_roles(poses)
	var tpose := {}
	for role in want:
		if not poses.has(role):
			print("xr_demo: cannot calibrate, no pose for ", role)
			return false
		tpose[role] = poses[role]
	if not (tpose.has("head") and tpose.has("left_hand") and tpose.has("right_hand")):
		print("xr_demo: cannot calibrate without head and both hands")
		return false
	calibration = Calibration.calibrate(skeleton, tpose)
	avatar.global_transform = calibration["root"]
	if adapter == null:
		roles = calibration["offsets"].keys()
		match String(opts["ik"]):
			"renik": adapter = Harness.RenIKAdapter.new()
			"none": adapter = Harness.IKAdapter.new()
			_: adapter = Harness.BuiltinAdapter.new()
		adapter.setup(world, skeleton, {"roles": roles})
	calibrations += 1
	_place_spectator()
	var root: Transform3D = calibration["root"]
	print("xr_demo: calibrated #%d, root yaw %.2f deg at (%.3f, %.3f), height ratio %.3f, roles %s" % [
		calibrations, rad_to_deg(root.basis.get_euler().y), root.origin.x, root.origin.z, calibration["height_ratio"], ",".join(roles)])
	return true

func _marker_for(role: String) -> MeshInstance3D:
	if not markers.has(role):
		var box := BoxMesh.new()
		box.size = Vector3(0.05, 0.03, 0.08)
		var mat := StandardMaterial3D.new()
		mat.albedo_color = Color(0.2, 0.75, 0.95)
		var m := MeshInstance3D.new()
		m.name = "Tracker_" + role
		m.mesh = box
		m.material_override = mat
		m.layers = LAYER_HEAD if role == "head" else LAYER_WORLD
		world.add_child(m)
		markers[role] = m
	return markers[role]

func tick() -> void:
	frames += 1
	if frames == 1:
		_place_spectator()
	driver.poll()
	var poses := tracker_poses()
	for role in markers:
		markers[role].visible = poses.has(role)
	for role in poses:
		_marker_for(role).global_transform = poses[role]

	if debug:
		_debug_motion(poses)

	var left: XRController3D = controllers["left_hand"]
	var right: XRController3D = controllers["right_hand"]
	var down := left.get_float(&"trigger") >= TRIGGER_PRESSED and right.get_float(&"trigger") >= TRIGGER_PRESSED
	if down and not triggers_were_down:
		calibrate(poses)
	triggers_were_down = down

	if adapter != null:
		var have_all := true
		for role in roles:
			have_all = have_all and poses.has(role)
		if have_all:
			adapter.apply_frame(self, poses)
	_write_status(poses)
	if int(opts["quit-after"]) > 0 and frames >= int(opts["quit-after"]):
		quit(0)

# IKH_DEBUG=1: report trackers whose pose changed since the previous frame (jitter check).
func _debug_motion(poses: Dictionary) -> void:
	var moved := []
	for role in poses:
		if debug_last.has(role):
			var a: Transform3D = debug_last[role]
			var b: Transform3D = poses[role]
			var dp := a.origin.distance_to(b.origin)
			var dr := rad_to_deg(a.basis.get_rotation_quaternion().angle_to(b.basis.get_rotation_quaternion()))
			if dp > 0.0 or dr > 0.0:
				moved.append("%s %.6f m %.5f deg" % [role, dp, dr])
	debug_last = poses
	if not moved.is_empty():
		print("xr_demo: frame %d moved: %s" % [frames, "; ".join(moved)])

func _write_status(poses: Dictionary) -> void:
	if String(opts["status"]) == "":
		return
	var left := XRServer.get_tracker(&"left_hand") as XRPositionalTracker
	var doc := {
		"state": "calibrated" if calibrations > 0 else ("ready" if focused else "starting"),
		"calibrations": calibrations,
		"roles": roles,
		"tracked": poses.keys(),
		"driver": driver.is_connected_to_driver(),
		"profile": left.profile if left != null else "",
		"window": [get_root().size.x, get_root().size.y],
		"fps": int(Engine.get_frames_per_second()),
	}
	if calibrations > 0:
		var root: Transform3D = calibration["root"]
		doc["root"] = {"position": [root.origin.x, root.origin.y, root.origin.z], "yaw_deg": rad_to_deg(root.basis.get_euler().y)}
		doc["height_ratio"] = calibration["height_ratio"]
	var text := JSON.stringify(doc)
	if text == last_status:
		return
	last_status = text
	var tmp := String(opts["status"]) + ".tmp"
	var f := FileAccess.open(tmp, FileAccess.WRITE)
	if f == null:
		return
	f.store_string(text)
	f.close()
	DirAccess.rename_absolute(tmp, String(opts["status"]))

class Ticker extends Node:
	var demo
	func _process(_delta: float) -> void:
		demo.tick()
