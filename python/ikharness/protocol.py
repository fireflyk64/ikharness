"""Client for the Monado ``ikharness`` driver wire protocol.

The layout mirrors ``monado/driver/ikharness/ikh_protocol.h`` exactly: every
message is a 12-byte header followed by a payload, little-endian, packed.

Coordinate convention: OpenXR / Godot style, right handed, +Y up, -Z forward,
meters, quaternions as (x, y, z, w), poses in STAGE space (floor origin).

Typical use::

    with IkhClient() as c:
        c.send_frame({"hmd": Pose((0, 1.6, 0)), "waist": Pose((0, 0.95, 0))}, frame_id=1)

Device names accepted by :meth:`IkhClient.send_frame` are the tracker roles
from the driver config plus ``"hmd"``, ``"left_hand"`` and ``"right_hand"``
(``"left"``/``"right"`` also work). Integer device indices are accepted too.
"""

from __future__ import annotations

import enum
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

MAGIC = 0x31484B49  # "IKH1"
VERSION = 1
DEFAULT_PORT = 4343
DEFAULT_HOST = "127.0.0.1"

_HEADER = struct.Struct("<IHHI")  # magic, version, type, payload_size
_DEVICE_DESC = struct.Struct("<II32s32s")  # index, kind, role, serial
_HELLO = struct.Struct("<II")  # device_count, reserved
_POSE = struct.Struct("<II3f4f3f3f")  # index, flags, pos, quat, linvel, angvel
_FRAME = struct.Struct("<QqII")  # frame_id, timestamp_ns, pose_count, reserved
_ACK = struct.Struct("<Qq")  # frame_id, applied_at_ns


class MsgType(enum.IntEnum):
    HELLO = 1
    FRAME = 2
    ACK = 3
    PING = 4
    PONG = 5
    QUERY = 6


class DeviceKind(enum.IntEnum):
    HMD = 1
    CONTROLLER_LEFT = 2
    CONTROLLER_RIGHT = 3
    TRACKER = 4


class PoseFlags(enum.IntFlag):
    ORIENTATION_VALID = 1 << 0
    POSITION_VALID = 1 << 1
    TRACKED = 1 << 2
    LINEAR_VELOCITY_VALID = 1 << 3
    ANGULAR_VELOCITY_VALID = 1 << 4
    CONNECTED = 1 << 5
    DEFAULT = ORIENTATION_VALID | POSITION_VALID | TRACKED | CONNECTED


Vec3 = Tuple[float, float, float]
Quat = Tuple[float, float, float, float]


@dataclass
class Pose:
    """A device pose in stage space."""

    position: Vec3 = (0.0, 0.0, 0.0)
    orientation: Quat = (0.0, 0.0, 0.0, 1.0)  # x, y, z, w
    linear_velocity: Vec3 = (0.0, 0.0, 0.0)
    angular_velocity: Vec3 = (0.0, 0.0, 0.0)
    flags: PoseFlags = PoseFlags.DEFAULT

    @staticmethod
    def disconnected() -> "Pose":
        """A pose that makes the device report as not connected / not tracked."""
        return Pose(flags=PoseFlags(0))


@dataclass(frozen=True)
class DeviceDesc:
    index: int
    kind: DeviceKind
    role: str
    serial: str

    @property
    def name(self) -> str:
        """The name accepted by :meth:`IkhClient.send_frame` for this device."""
        if self.kind == DeviceKind.HMD:
            return "hmd"
        if self.kind == DeviceKind.CONTROLLER_LEFT:
            return "left_hand"
        if self.kind == DeviceKind.CONTROLLER_RIGHT:
            return "right_hand"
        return self.role


@dataclass
class Ack:
    frame_id: int
    applied_at_ns: int


class ProtocolError(RuntimeError):
    pass


class IkhClient:
    """Blocking TCP client for the driver.

    The constructor connects and consumes the HELLO message, after which
    :attr:`devices` describes every device the driver publishes.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 5.0):
        self.host = host
        self.port = port
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.devices: List[DeviceDesc] = []
        self._by_name: Dict[str, DeviceDesc] = {}
        self.last_ack: Optional[Ack] = None
        self._read_hello()

    # -- context manager -------------------------------------------------

    def __enter__(self) -> "IkhClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None  # type: ignore[assignment]

    # -- low level ---------------------------------------------------------

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ProtocolError("driver closed the connection")
            buf += chunk
        return bytes(buf)

    def _recv_msg(self) -> Tuple[MsgType, bytes]:
        magic, version, mtype, size = _HEADER.unpack(self._recv_exact(_HEADER.size))
        if magic != MAGIC:
            raise ProtocolError(f"bad magic 0x{magic:08x}")
        if version != VERSION:
            raise ProtocolError(f"unsupported protocol version {version}")
        payload = self._recv_exact(size) if size else b""
        return MsgType(mtype), payload

    def _send_msg(self, mtype: MsgType, payload: bytes = b"") -> None:
        self._sock.sendall(_HEADER.pack(MAGIC, VERSION, int(mtype), len(payload)) + payload)

    def _parse_hello(self, payload: bytes) -> None:
        count, _ = _HELLO.unpack_from(payload, 0)
        devices = []
        off = _HELLO.size
        for _ in range(count):
            index, kind, role, serial = _DEVICE_DESC.unpack_from(payload, off)
            off += _DEVICE_DESC.size
            devices.append(
                DeviceDesc(
                    index=index,
                    kind=DeviceKind(kind),
                    role=role.split(b"\0", 1)[0].decode(),
                    serial=serial.split(b"\0", 1)[0].decode(),
                )
            )
        self.devices = devices
        self._by_name = {}
        for d in devices:
            self._by_name[d.name] = d
            self._by_name[d.serial] = d
        # Convenience aliases.
        if "left_hand" in self._by_name:
            self._by_name.setdefault("left", self._by_name["left_hand"])
        if "right_hand" in self._by_name:
            self._by_name.setdefault("right", self._by_name["right_hand"])

    def _read_hello(self) -> None:
        mtype, payload = self._recv_msg()
        if mtype != MsgType.HELLO:
            raise ProtocolError(f"expected HELLO, got {mtype.name}")
        self._parse_hello(payload)

    # -- public API ----------------------------------------------------------

    @property
    def device_names(self) -> List[str]:
        return [d.name for d in self.devices]

    def device(self, name_or_index: Union[str, int]) -> DeviceDesc:
        if isinstance(name_or_index, int):
            return self.devices[name_or_index]
        try:
            return self._by_name[name_or_index]
        except KeyError:
            raise KeyError(f"unknown device {name_or_index!r}; known: {self.device_names}") from None

    def send_frame(
        self,
        poses: Mapping[Union[str, int], Pose],
        frame_id: int = 0,
        timestamp_ns: int = 0,
        wait_ack: bool = True,
    ) -> Optional[Ack]:
        """Send poses for a subset of devices; unlisted devices keep their pose.

        Returns the driver's ACK (when ``wait_ack``), which carries the frame id
        and the driver's monotonic clock at the time the poses went live.
        """
        body = bytearray(_FRAME.pack(frame_id, timestamp_ns, len(poses), 0))
        for key, pose in poses.items():
            d = self.device(key)
            body += _POSE.pack(
                d.index,
                int(pose.flags),
                *pose.position,
                *pose.orientation,
                *pose.linear_velocity,
                *pose.angular_velocity,
            )
        self._send_msg(MsgType.FRAME, bytes(body))
        if not wait_ack:
            return None
        mtype, payload = self._recv_msg()
        if mtype != MsgType.ACK:
            raise ProtocolError(f"expected ACK, got {mtype.name}")
        self.last_ack = Ack(*_ACK.unpack(payload))
        return self.last_ack

    def ping(self) -> Ack:
        self._send_msg(MsgType.PING)
        mtype, payload = self._recv_msg()
        if mtype != MsgType.PONG:
            raise ProtocolError(f"expected PONG, got {mtype.name}")
        return Ack(*_ACK.unpack(payload))

    def query(self) -> List[DeviceDesc]:
        self._send_msg(MsgType.QUERY)
        mtype, payload = self._recv_msg()
        if mtype != MsgType.HELLO:
            raise ProtocolError(f"expected HELLO, got {mtype.name}")
        self._parse_hello(payload)
        return self.devices


def wait_for_driver(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 30.0) -> IkhClient:
    """Retry connecting until the driver accepts, useful right after starting the service."""
    deadline = time.monotonic() + timeout
    last_error: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            return IkhClient(host, port)
        except OSError as e:  # connection refused while the service starts
            last_error = e
            time.sleep(0.2)
    raise TimeoutError(f"driver not reachable at {host}:{port} after {timeout}s: {last_error}")
