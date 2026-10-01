# Minimal client for the Monado ikharness driver's wire protocol (see
# monado/driver/ikharness/ikh_protocol.h), used by the XR demo to read the poses of
# generic trackers. Godot can only get those through XR_HTCX_vive_tracker_interaction,
# which Monado does not implement, so they come through this side channel; head, hands
# and buttons still arrive through OpenXR.
#
# Non-blocking: call poll() every frame; `state` holds the latest answer.
extends RefCounted

const MAGIC := 0x31484B49
const MSG_HELLO := 1
const MSG_QUERY := 6
const MSG_GET_STATE := 8
const MSG_STATE := 9
const KIND_HMD := 1
const KIND_LEFT := 2
const KIND_RIGHT := 3
const FLAG_ORIENTATION_VALID := 1
const FLAG_POSITION_VALID := 2
const FLAG_CONNECTED := 32

var tcp := StreamPeerTCP.new()
var names := {}          # device index -> name ("hmd", "left_hand", "right_hand", tracker role)
var state := {}          # name -> Transform3D, only devices that are connected with a valid pose
var frame_id := -1       # id of the last pose frame the driver applied
var updates := 0         # number of STATE messages received
var _buf := PackedByteArray()
var _awaiting := false

func open(host: String, port: int) -> bool:
	tcp.big_endian = false
	return tcp.connect_to_host(host, port) == OK

func is_connected_to_driver() -> bool:
	return tcp.get_status() == StreamPeerTCP.STATUS_CONNECTED and not names.is_empty()

func _send(type: int) -> void:
	var b := StreamPeerBuffer.new()
	b.big_endian = false
	b.put_u32(MAGIC)
	b.put_u16(1)
	b.put_u16(type)
	b.put_u32(0)
	tcp.put_data(b.data_array)

func poll() -> void:
	tcp.poll()
	if tcp.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		return
	var n := tcp.get_available_bytes()
	if n > 0:
		var r: Array = tcp.get_partial_data(n)
		if r[0] == OK:
			_buf.append_array(r[1])
	while _buf.size() >= 12:
		var size := _buf.decode_u32(8)
		if _buf.decode_u32(0) != MAGIC or size > 65536:
			push_error("ikharness driver: bad message header")
			tcp.disconnect_from_host()
			return
		if _buf.size() < 12 + size:
			break
		_handle(_buf.decode_u16(6), _buf.slice(12, 12 + size))
		_buf = _buf.slice(12 + size)
	if not names.is_empty() and not _awaiting:
		_send(MSG_GET_STATE)
		_awaiting = true

func _cstr(b: PackedByteArray, off: int, length: int) -> String:
	var raw := b.slice(off, off + length)
	var end := raw.find(0)
	return (raw.slice(0, end) if end >= 0 else raw).get_string_from_utf8()

func _handle(type: int, p: PackedByteArray) -> void:
	if type == MSG_HELLO:
		names.clear()
		for i in range(p.decode_u32(0)):
			var off := 8 + i * 72
			var kind := p.decode_u32(off + 4)
			var name := _cstr(p, off + 8, 32)
			match kind:
				KIND_HMD: name = "hmd"
				KIND_LEFT: name = "left_hand"
				KIND_RIGHT: name = "right_hand"
			names[p.decode_u32(off)] = name
	elif type == MSG_STATE:
		_awaiting = false
		updates += 1
		frame_id = p.decode_s64(0)
		var fresh := {}
		for i in range(p.decode_u32(16)):
			var off := 24 + i * 60
			var flags := p.decode_u32(off + 4)
			var need := FLAG_ORIENTATION_VALID | FLAG_POSITION_VALID | FLAG_CONNECTED
			if flags & need != need:
				continue
			var pos := Vector3(p.decode_float(off + 8), p.decode_float(off + 12), p.decode_float(off + 16))
			var q := Quaternion(p.decode_float(off + 20), p.decode_float(off + 24), p.decode_float(off + 28), p.decode_float(off + 32))
			fresh[names.get(p.decode_u32(off), "device%d" % p.decode_u32(off))] = Transform3D(Basis(q.normalized()), pos)
		state = fresh
