# CPU ShaderMotion encoder for the harness: solved global bone poses -> slot values -> PNG.
# Mirrors python/ikharness/shadermotion (codec.py, humanoid.py); the Python decoder reads
# these images back, which cross-checks the two implementations. No renderer needed.
extends RefCounted

const TransformUtil := preload("res://addons/humanoid/transform_util.gd")
const HumanTrait := preload("res://addons/humanoid/human_trait.gd")

const RADIX := 3
const DIGITS := 6
const POW := 729
const HALF := 364.0
const GRID_W := 80
const GRID_H := 45
const SLOT_ROWS := 45

static func gray_digits(n: int) -> PackedInt32Array:
	var digits := PackedInt32Array()
	digits.resize(DIGITS)
	var rem := n
	for i in range(DIGITS - 1, -1, -1):
		digits[i] = rem % RADIX
		rem = rem / RADIX
	var stored := PackedInt32Array()
	stored.resize(DIGITS)
	var state := 0
	for i in range(DIGITS):
		stored[i] = digits[i] if (state & 1) == 0 else RADIX - 1 - digits[i]
		state = state * RADIX + digits[i]
	return stored

# A number in [-1, 1] -> two colors on the continuous base-3 Gray curve (G,R,B,G,R,B order).
static func encode_snorm(value: float) -> Array[Color]:
	var t: float = (clampf(value, -1.0, 1.0) + 1.0) * HALF
	var lo: int = int(floor(t))
	var frac: float = t - lo
	if lo >= POW - 1:
		lo = POW - 1
		frac = 0.0
	var a := gray_digits(lo)
	var ch: Array[float] = []
	if frac > 0.0:
		var b := gray_digits(lo + 1)
		for i in range(DIGITS):
			ch.append((a[i] + (b[i] - a[i]) * frac) / float(RADIX - 1))
	else:
		for i in range(DIGITS):
			ch.append(a[i] / float(RADIX - 1))
	return [Color(ch[1], ch[0], ch[2]), Color(ch[4], ch[3], ch[5])]

# Wide range float -> (hi, lo) slot values, see codec.encode_float.
static func encode_float(value: float) -> Vector2:
	var t: float = clampf(value * HALF + (POW * POW - 1) / 2.0, 0.0, POW * POW - 1.0)
	var n: int = int(floor(t + 0.5))
	var f: float = t - n
	var r: int = n / POW
	var x: int = n % POW
	var h: float = r
	var lf: float = f
	if x == 0 and f < 0.0:
		h = r + f
		lf = 0.0
	elif x == POW - 1 and f > 0.0:
		h = r + f
		lf = 0.0
	var l: float = x + lf
	if (r & 1) != 0:
		l = POW - 1 - l
	return Vector2((h - HALF) / HALF, (l - HALF) / HALF)

# Godot bone name -> [first slot, channels]; same table as codec.BONE_SLOTS.
static func bone_slots() -> Dictionary:
	var d := {
		"Spine": [12, [0, 1, 2]], "Chest": [15, [0, 1, 2]], "UpperChest": [18, [0, 1, 2]],
		"Neck": [21, [0, 1, 2]], "Head": [24, [0, 1, 2]],
		"LeftUpperLeg": [27, [0, 1, 2]], "RightUpperLeg": [30, [0, 1, 2]],
		"LeftLowerLeg": [33, [0, 1, 2]], "RightLowerLeg": [36, [0, 1, 2]],
		"LeftFoot": [39, [0, 1, 2]], "RightFoot": [42, [0, 1, 2]],
		"LeftShoulder": [45, [0, 1, 2]], "RightShoulder": [48, [0, 1, 2]],
		"LeftUpperArm": [51, [0, 1, 2]], "RightUpperArm": [54, [0, 1, 2]],
		"LeftLowerArm": [57, [0, 1, 2]], "RightLowerArm": [60, [0, 1, 2]],
		"LeftHand": [63, [0, 1, 2]], "RightHand": [66, [0, 1, 2]],
		"LeftToes": [69, [2]], "RightToes": [70, [2]],
		"LeftEye": [71, [1, 2]], "RightEye": [73, [1, 2]], "Jaw": [75, [1, 2]],
	}
	var fingers := ["Thumb", "Index", "Middle", "Ring", "Little"]
	var sides := {"Left": 90, "Right": 110}
	for side in sides:
		for i in range(fingers.size()):
			var s: int = sides[side] + 4 * i
			var names: Array = ["Metacarpal", "Proximal", "Distal"] if fingers[i] == "Thumb" else ["Proximal", "Intermediate", "Distal"]
			d[side + fingers[i] + names[0]] = [s, [1, 2]]
			d[side + fingers[i] + names[1]] = [s + 2, [2]]
			d[side + fingers[i] + names[2]] = [s + 3, [2]]
	return d

# Quaternion -> (twist x, swing y, swing z) radians; exact inverse of TransformUtil.swing_twist.
# Own implementation (same as python humanoid.swing_twist_inv): picks the w >= 0 representative
# and solves the 2x2 system directly, so it has no divisions by near-zero twist terms.
static func swing_twist_inv(q: Quaternion) -> Vector3:
	var a := q.x
	var b := q.y
	var c := q.z
	var d := q.w
	if d < 0.0:
		a = -a; b = -b; c = -c; d = -d
	var n := a * a + d * d
	var twist_x := sqrt(a * a / n) if n > 1e-16 else 0.0
	twist_x = minf(twist_x, 1.0)
	if a < 0.0:
		twist_x = -twist_x
	var twist_w := sqrt(maxf(0.0, 1.0 - twist_x * twist_x))
	var swing_w := d / twist_w if absf(twist_x) < 1e-8 else a / twist_x
	swing_w = clampf(swing_w, -1.0, 1.0)
	var x := asin(twist_x) * 2.0
	var yz := acos(swing_w) * 2.0
	var sinc := 0.5 if absf(yz) < 1e-8 else sin(yz / 2.0) / yz
	var y := (b * twist_w - c * twist_x) / sinc
	var z := (b * twist_x + c * twist_w) / sinc
	return Vector3(x, y, z)

# Godot bone-local pose rotation -> {"slots": Vector3, "leftover": Quaternion, "projection_deg": float}.
# leftover_in is the parent's residual (what the parent's joint could not express).
static func encode_bone(bone_idx: int, rotation: Quaternion, leftover_in: Quaternion) -> Dictionary:
	var mfb: PackedInt32Array = HumanTrait.MuscleFromBone[bone_idx]
	var rot := (leftover_in * rotation).normalized()
	var pre: Quaternion = HumanTrait.preQ_exported[bone_idx]
	var inv_post: Quaternion = HumanTrait.postQ_inverse_exported[bone_idx]
	var swing := (pre.inverse() * rot * inv_post.inverse()).normalized()
	var st := swing_twist_inv(swing)
	for i in range(3):
		if mfb[i] == -1:
			st[i] = 0.0
	var slots: Vector3 = st * (Vector3(1, -1, -1) * HumanTrait.Signs[bone_idx])
	for i in range(3):
		if mfb[i] == -1:
			slots[i] = 0.0
		else:
			slots[i] = wrapf(slots[i] / PI + 1.0, 0.0, 2.0) - 1.0
	var decoded: Quaternion = TransformUtil.calculate_humanoid_rotation(bone_idx, slots)
	var residual := (decoded.inverse() * rot).normalized()
	return {"slots": slots, "leftover": residual, "projection_deg": rad_to_deg(2.0 * acos(clampf(absf(residual.w), 0.0, 1.0)))}

# globals: bone name -> Transform3D in skeleton space (solved pose). Returns {slot: value}.
static func pose_to_slots(skeleton: Skeleton3D, globals: Dictionary, hips_height: float) -> Dictionary:
	var slots := {}
	var hips: Transform3D = globals["Hips"]
	# Unity is left handed with +X to the character's right: mirror X.
	var p := Vector3(-hips.origin.x, hips.origin.y, hips.origin.z)
	var basis := hips.basis.orthonormalized()
	var ry := Vector3(-basis.y.x, basis.y.y, basis.y.z).normalized()
	var rz := Vector3(-basis.z.x, basis.z.y, basis.z.z).normalized()
	for axis in range(3):
		var hl := encode_float(p[axis] / 2.0)
		slots[axis] = hl.x
		slots[3 + axis] = hl.y
	var ly: float = 1.0 if hips_height >= 1.0 else hips_height
	var lz: float = 1.0 / hips_height if hips_height >= 1.0 else 1.0
	for axis in range(3):
		slots[6 + axis] = ry[axis] * ly
		slots[9 + axis] = rz[axis] * lz

	var table := bone_slots()
	var index_of := {}
	for i in range(HumanTrait.GodotHumanNames.size()):
		index_of[HumanTrait.GodotHumanNames[i]] = i
	var leftovers := {}  # bone name -> residual rotation handed to its children
	for b in range(skeleton.get_bone_count()):
		var name := skeleton.get_bone_name(b)
		if name == "Hips" or not table.has(name) or not index_of.has(name) or not globals.has(name):
			continue
		var parent := skeleton.get_bone_parent(b)
		if parent < 0 or not globals.has(skeleton.get_bone_name(parent)):
			continue
		var pg: Transform3D = globals[skeleton.get_bone_name(parent)]
		var g: Transform3D = globals[name]
		var local := (pg.basis.orthonormalized().inverse() * g.basis.orthonormalized()).get_rotation_quaternion()
		var parent_name := skeleton.get_bone_name(parent)
		var enc: Dictionary = encode_bone(index_of[name], local, leftovers.get(parent_name, Quaternion.IDENTITY))
		leftovers[name] = enc["leftover"]
		var triplet: Vector3 = enc["slots"]
		var entry: Array = table[name]
		var channels: Array = entry[1]
		for i in range(channels.size()):
			slots[int(entry[0]) + i] = triplet[channels[i]]
	return slots

static func slots_to_image(slots: Dictionary, width: int = 640, height: int = 360) -> Image:
	var img := Image.create_empty(width, height, false, Image.FORMAT_RGB8)
	img.fill(Color.BLACK)
	var sq_w := float(width) / GRID_W
	var sq_h := float(height) / GRID_H
	for slot in slots:
		var col: int = int(slot) / SLOT_ROWS
		var row: int = int(slot) % SLOT_ROWS
		var colors := encode_snorm(slots[slot])
		for k in range(2):
			var x0 := int(round((col * 2 + k) * sq_w))
			var x1 := int(round((col * 2 + k + 1) * sq_w))
			var y0 := int(round(row * sq_h))
			var y1 := int(round((row + 1) * sq_h))
			img.fill_rect(Rect2i(x0, y0, x1 - x0, y1 - y0), colors[k])
	return img
