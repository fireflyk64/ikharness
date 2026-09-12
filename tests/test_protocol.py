"""Unit tests for the wire protocol client against a fake in-process server."""

import socket
import struct
import threading

from ikharness import protocol as P
from ikharness.protocol import DeviceKind, IkhClient, Pose, PoseFlags


def test_struct_sizes_match_c_header():
    assert P._HEADER.size == 12
    assert P._DEVICE_DESC.size == 72
    assert P._HELLO.size == 8
    assert P._POSE.size == 60
    assert P._FRAME.size == 24
    assert P._ACK.size == 16
    assert P.MAGIC == 0x31484B49
    assert struct.pack("<I", P.MAGIC) == b"IKH1"


class FakeDriver(threading.Thread):
    """Speaks just enough of the protocol to exercise the client."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.frames = []
        self.devices = [
            (0, DeviceKind.HMD, "", "IKH-HMD"),
            (1, DeviceKind.CONTROLLER_LEFT, "", "IKH-CTRL-L"),
            (2, DeviceKind.CONTROLLER_RIGHT, "", "IKH-CTRL-R"),
            (3, DeviceKind.TRACKER, "waist", "IKH-TRK-waist"),
        ]

    def _send(self, conn, mtype, payload):
        conn.sendall(P._HEADER.pack(P.MAGIC, P.VERSION, int(mtype), len(payload)) + payload)

    def _hello(self, conn):
        body = P._HELLO.pack(len(self.devices), 0)
        for idx, kind, role, serial in self.devices:
            body += P._DEVICE_DESC.pack(idx, int(kind), role.encode(), serial.encode())
        self._send(conn, P.MsgType.HELLO, body)

    def run(self):
        conn, _ = self.sock.accept()
        with conn:
            self._hello(conn)
            last = 0
            while True:
                hdr = conn.recv(P._HEADER.size, socket.MSG_WAITALL)
                if not hdr:
                    return
                magic, version, mtype, size = P._HEADER.unpack(hdr)
                assert magic == P.MAGIC and version == P.VERSION
                payload = conn.recv(size, socket.MSG_WAITALL) if size else b""
                if mtype == P.MsgType.FRAME:
                    frame_id, ts, count, _ = P._FRAME.unpack_from(payload, 0)
                    poses = [P._POSE.unpack_from(payload, P._FRAME.size + i * P._POSE.size) for i in range(count)]
                    self.frames.append((frame_id, ts, poses))
                    last = frame_id
                    self._send(conn, P.MsgType.ACK, P._ACK.pack(frame_id, 123456789))
                elif mtype == P.MsgType.PING:
                    self._send(conn, P.MsgType.PONG, P._ACK.pack(last, 42))
                elif mtype == P.MsgType.QUERY:
                    self._hello(conn)


def test_client_round_trip():
    server = FakeDriver()
    server.start()
    with IkhClient("127.0.0.1", server.port) as c:
        assert c.device_names == ["hmd", "left_hand", "right_hand", "waist"]
        assert c.device("left").index == 1
        assert c.device("IKH-TRK-waist").role == "waist"

        ack = c.send_frame(
            {"hmd": Pose((1, 2, 3), (0, 0, 0, 1)), "waist": Pose((4, 5, 6), (0, 1, 0, 0), flags=PoseFlags.DEFAULT | PoseFlags.LINEAR_VELOCITY_VALID, linear_velocity=(0.1, 0.2, 0.3))},
            frame_id=9,
            timestamp_ns=77,
        )
        assert ack.frame_id == 9 and ack.applied_at_ns == 123456789
        assert c.ping().frame_id == 9
        assert [d.serial for d in c.query()] == ["IKH-HMD", "IKH-CTRL-L", "IKH-CTRL-R", "IKH-TRK-waist"]

    frame_id, ts, poses = server.frames[0]
    assert (frame_id, ts) == (9, 77)
    assert poses[0][0] == 0 and poses[0][2:5] == (1.0, 2.0, 3.0)
    assert poses[1][0] == 3 and poses[1][1] == int(PoseFlags.DEFAULT | PoseFlags.LINEAR_VELOCITY_VALID)
    assert poses[1][5:9] == (0.0, 1.0, 0.0, 0.0)
    assert [round(v, 6) for v in poses[1][9:12]] == [0.1, 0.2, 0.3]
