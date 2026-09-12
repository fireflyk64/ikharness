"""Small quaternion / transform helpers (x, y, z, w convention, numpy based)."""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import numpy as np

Vec3 = np.ndarray
Quat = np.ndarray

IDENTITY_QUAT = np.array([0.0, 0.0, 0.0, 1.0])


def quat(x: Sequence[float]) -> Quat:
    q = np.asarray(x, dtype=float)
    n = np.linalg.norm(q)
    return q / n if n > 0 else IDENTITY_QUAT.copy()


def quat_mul(a: Quat, b: Quat) -> Quat:
    """Hamilton product a*b (apply b first, then a)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def quat_inv(q: Quat) -> Quat:
    return np.array([-q[0], -q[1], -q[2], q[3]])


def quat_rotate(q: Quat, v: Vec3) -> Vec3:
    """Rotate vector v by unit quaternion q."""
    u = q[:3]
    w = q[3]
    v = np.asarray(v, dtype=float)
    return 2.0 * np.dot(u, v) * u + (w * w - np.dot(u, u)) * v + 2.0 * w * np.cross(u, v)


def quat_angle(a: Quat, b: Quat) -> float:
    """Angle in radians between two rotations (0 when equal, sign agnostic)."""
    d = abs(float(np.dot(a, b)))
    d = min(1.0, max(-1.0, d))
    return 2.0 * math.acos(d)


def quat_from_axis_angle(axis: Sequence[float], angle: float) -> Quat:
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    s = math.sin(angle / 2)
    return np.array([a[0] * s, a[1] * s, a[2] * s, math.cos(angle / 2)])


def quat_to_matrix(q: Quat) -> np.ndarray:
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def quat_from_matrix(m: np.ndarray) -> Quat:
    m = np.asarray(m, dtype=float)
    t = np.trace(m)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return quat([(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s])
    i = int(np.argmax([m[0, 0], m[1, 1], m[2, 2]]))
    if i == 0:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        return quat([0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s])
    if i == 1:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        return quat([(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s])
    s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
    return quat([(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s])


class Transform:
    """Rigid transform: rotation (quaternion x,y,z,w) then translation."""

    __slots__ = ("position", "rotation")

    def __init__(self, position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0)):
        self.position = np.asarray(position, dtype=float)
        self.rotation = quat(rotation)

    @staticmethod
    def identity() -> "Transform":
        return Transform()

    def __mul__(self, other: "Transform") -> "Transform":
        """self * other: apply other first, then self (like Godot's Transform3D)."""
        return Transform(self.position + quat_rotate(self.rotation, other.position), quat_mul(self.rotation, other.rotation))

    def inverse(self) -> "Transform":
        inv = quat_inv(self.rotation)
        return Transform(-quat_rotate(inv, self.position), inv)

    def apply(self, point: Sequence[float]) -> Vec3:
        return self.position + quat_rotate(self.rotation, np.asarray(point, dtype=float))

    def as_lists(self) -> Tuple[list, list]:
        return [float(v) for v in self.position], [float(v) for v in self.rotation]

    def __repr__(self) -> str:
        p = ", ".join(f"{v:+.4f}" for v in self.position)
        r = ", ".join(f"{v:+.4f}" for v in self.rotation)
        return f"Transform(pos=({p}), rot=({r}))"


def yaw180() -> Quat:
    """Rotation of 180 degrees about +Y: converts dataset space (faces +Z) to OpenXR stage space (faces -Z)."""
    return np.array([0.0, 1.0, 0.0, 0.0])
