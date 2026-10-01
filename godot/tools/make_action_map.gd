# Writes godot/harness/openxr_action_map.tres: the XR demo's action map.
#
#   godot --headless -s godot/tools/make_action_map.gd -- /abs/godot/harness/openxr_action_map.tres
#
# Godot's built-in default map has no Valve Index profile, and an editor build saves that
# default into the project when the file is missing; this one is small and explicit.
# Actions use Godot's standard names (default_pose, aim_pose, grip_pose, trigger, ...), so
# XRController3D works as usual. Body trackers are bound through the Vive tracker profile of
# XR_HTCX_vive_tracker_interaction (tracker names "/user/vive_tracker_htcx/role/<role>").
extends SceneTree

const HANDS := ["/user/hand/left", "/user/hand/right"]
# Body trackers through XR_HTCX_vive_tracker_interaction (roles the harness uses).
const TRACKER_ROLES := ["waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow", "right_elbow",
	"left_shoulder", "right_shoulder"]
const TRACKER_PREFIX := "/user/vive_tracker_htcx/role/"

func _action(set: OpenXRActionSet, name: String, label: String, type: int, paths: Array = HANDS) -> OpenXRAction:
	var a := OpenXRAction.new()
	a.resource_name = name
	a.localized_name = label
	a.action_type = type
	a.toplevel_paths = PackedStringArray(paths)
	set.add_action(a)
	return a

func _init():
	var out := OS.get_cmdline_user_args()
	if out.is_empty():
		printerr("usage: -s make_action_map.gd -- /abs/path/openxr_action_map.tres")
		quit(1)
		return
	var map := OpenXRActionMap.new()
	var set := OpenXRActionSet.new()
	set.resource_name = "ikharness"
	set.localized_name = "IK harness"
	map.add_action_set(set)
	var tracker_paths := []
	for role in TRACKER_ROLES:
		tracker_paths.append(TRACKER_PREFIX + role)
	var a := {
		"default_pose": _action(set, "default_pose", "Default pose", OpenXRAction.OPENXR_ACTION_POSE, HANDS + tracker_paths),
		"aim_pose": _action(set, "aim_pose", "Aim pose", OpenXRAction.OPENXR_ACTION_POSE),
		"grip_pose": _action(set, "grip_pose", "Grip pose", OpenXRAction.OPENXR_ACTION_POSE),
		"trigger": _action(set, "trigger", "Trigger", OpenXRAction.OPENXR_ACTION_FLOAT),
		"trigger_click": _action(set, "trigger_click", "Trigger click", OpenXRAction.OPENXR_ACTION_BOOL),
		"grip": _action(set, "grip", "Grip", OpenXRAction.OPENXR_ACTION_FLOAT),
		"menu_button": _action(set, "menu_button", "Menu button", OpenXRAction.OPENXR_ACTION_BOOL),
	}
	var profiles := {
		"/interaction_profiles/valve/index_controller": {
			"default_pose": "input/aim/pose", "aim_pose": "input/aim/pose", "grip_pose": "input/grip/pose",
			"trigger": "input/trigger/value", "trigger_click": "input/trigger/click", "grip": "input/squeeze/value",
			"menu_button": "input/b/click",
		},
		"/interaction_profiles/oculus/touch_controller": {
			"default_pose": "input/aim/pose", "aim_pose": "input/aim/pose", "grip_pose": "input/grip/pose",
			"trigger": "input/trigger/value", "trigger_click": "input/trigger/value", "grip": "input/squeeze/value",
		},
		"/interaction_profiles/khr/simple_controller": {
			"default_pose": "input/aim/pose", "aim_pose": "input/aim/pose", "grip_pose": "input/grip/pose",
			"trigger": "input/select/click", "trigger_click": "input/select/click", "menu_button": "input/menu/click",
		},
	}
	for path in profiles:
		var ip := OpenXRInteractionProfile.new()
		ip.interaction_profile_path = path
		var bindings := []
		for name in profiles[path]:
			for hand in HANDS:
				var b := OpenXRIPBinding.new()
				b.action = a[name]
				b.binding_path = hand + "/" + profiles[path][name]
				bindings.append(b)
		ip.bindings = bindings
		map.add_interaction_profile(ip)
	# Vive trackers: the default pose of every role (the runtime skips this profile when it
	# lacks the extension).
	var trackers := OpenXRInteractionProfile.new()
	trackers.interaction_profile_path = "/interaction_profiles/htc/vive_tracker_htcx"
	var tracker_bindings := []
	for path in tracker_paths:
		var b := OpenXRIPBinding.new()
		b.action = a["default_pose"]
		b.binding_path = path + "/input/grip/pose"
		tracker_bindings.append(b)
	trackers.bindings = tracker_bindings
	map.add_interaction_profile(trackers)
	var err := ResourceSaver.save(map, out[0])
	print("make_action_map: ", out[0], " -> ", error_string(err))
	quit(0 if err == OK else 1)
