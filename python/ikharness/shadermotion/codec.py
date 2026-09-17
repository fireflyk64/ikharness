"""ShaderMotion codec: real numbers <-> colored squares, frame layout, hips float scheme.

A frame is an 80 x 45 grid of squares. Two horizontally adjacent squares form a *slot*
holding one real number in [-1, 1]; slots are indexed column-major (45 per column), so a
frame is a 40 x 45 matrix of numbers. A number is mapped onto a base-3 Gray curve through
the 6-cube and written as two colors in G,R,B,G,R,B order (sRGB values, 0..1).

Everything here is pure numpy so it can serve as the reference for the Godot and Unity
shaders and be tested without a GPU.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

RADIX = 3
TILE_LEN = 2  # colors per slot
DIGITS = TILE_LEN * 3
POW = RADIX ** DIGITS  # 729 levels per slot
HALF = (POW - 1) / 2.0  # 364

GRID_W, GRID_H = 80, 45
SLOT_COLS, SLOT_ROWS = GRID_W // TILE_LEN, GRID_H  # 40 x 45

POSITION_SCALE = 2.0  # hips position is stored divided by 2


# -- one number <-> two colors ---------------------------------------------------------

def _gray_digits(n: int) -> List[int]:
    """Base-3 reflected Gray code of ``n`` as DIGITS stored digits (most significant first)."""
    digits = []
    for i in range(DIGITS - 1, -1, -1):
        digits.append((n // (RADIX ** i)) % RADIX)
    stored = []
    state = 0
    for d in digits:
        stored.append(d if (state & 1) == 0 else RADIX - 1 - d)
        state = state * RADIX + d
    return stored


def encode_snorm(value: float) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """A number in [-1, 1] -> two RGB colors (floats 0..1). Continuous: neighbours differ slightly."""
    t = (min(1.0, max(-1.0, float(value))) + 1.0) * HALF  # 0 .. POW-1
    lo = int(math.floor(t))
    if lo >= POW - 1:
        lo, frac = POW - 1, 0.0
    else:
        frac = t - lo
    a = _gray_digits(lo)
    if frac > 0.0:
        b = _gray_digits(lo + 1)
        stored = [x + (y - x) * frac for x, y in zip(a, b)]  # exactly one digit moves
    else:
        stored = [float(x) for x in a]
    ch = [s / (RADIX - 1) for s in stored]
    # digits are consumed in g, r, b order per color
    return (ch[1], ch[0], ch[2]), (ch[4], ch[3], ch[5])


def _decoder_add(state: List[float], x: float) -> None:
    # HLSL gray_decoder_add: reflect on odd state, keep the fractional residual of the last
    # digit that is not pinned at an end of its range.
    if int(state[0]) & 1:
        x = RADIX - 1 - x
    r = float(round(x))
    state[0] = state[0] * RADIX + r
    res = x - r
    if r != 0.0:
        state[1] = res
    if r != RADIX - 1:
        state[2] = res


def _decoder_sum(state: Sequence[float]) -> float:
    px = max(0.0, state[2] * 2.0)
    py = max(0.0, state[1] * -2.0)
    mn, mx = min(px, py), max(px, py)
    return (mn / max(mx - px * py, 1e-5) * (px - py) + (px - py)) * 0.5 + state[0]


def decode_snorm(c0: Sequence[float], c1: Sequence[float]) -> float:
    """Two RGB colors (floats 0..1) -> the number in [-1, 1]."""
    state = [0.0, 0.0, 0.0]
    for c in (c0, c1):
        for channel in (c[1], c[0], c[2]):  # g, r, b
            _decoder_add(state, min(1.0, max(0.0, float(channel))) * (RADIX - 1))
    state[0] -= HALF
    return _decoder_sum(state) / HALF


# -- wide-range float in two slots (hips position) ----------------------------------------

def encode_float(value: float) -> Tuple[float, float]:
    """A number (|value| < ~730) -> (hi, lo) slot values. For |value| <= 1, hi == 0 and lo == value."""
    t = float(value) * HALF + (POW * POW - 1) / 2.0
    t = min(POW * POW - 1.0, max(0.0, t))
    n = int(math.floor(t + 0.5))
    f = t - n  # [-0.5, 0.5)
    r, x = divmod(n, POW)
    h = float(r)
    lf = f
    if x == 0 and f < 0.0:
        h, lf = r + f, 0.0
    elif x == POW - 1 and f > 0.0:
        h, lf = r + f, 0.0
    l = x + lf
    if r & 1:
        l = POW - 1 - l
    return (h - HALF) / HALF, (l - HALF) / HALF


def decode_float(hi: float, lo: float) -> float:
    """Inverse of :func:`encode_float` (port of ShaderImpl.DecodeVideoFloat)."""
    h = hi * HALF + HALF
    l = lo * HALF + HALF
    x = int(round(l))
    y = min(l - x, 0.0)
    z = max(l - x, 0.0)
    r = int(round(h))
    if r & 1:
        x, y, z = POW - 1 - x, -z, -y
    if x == 0:
        y += min(0.0, h - r)
    if x == POW - 1:
        z += max(0.0, h - r)
    xx = x + r * POW - (POW * POW - 1) / 2.0
    y += 0.5
    z -= 0.5
    return ((y + z) / max(abs(y), abs(z)) * 0.5 + xx) / HALF


# -- slot layout ---------------------------------------------------------------------------

#: bone -> (first slot, channel list); channels 0,1,2 are the swing-twist X,Y,Z angles.
#: Hips uses 12 slots: position hi xyz, position lo xyz, scaled rotation y-axis, z-axis.
BONE_SLOTS: Dict[str, Tuple[int, Tuple[int, ...]]] = {
    "Hips": (0, tuple(range(3, 15))),
    "Spine": (12, (0, 1, 2)), "Chest": (15, (0, 1, 2)), "UpperChest": (18, (0, 1, 2)),
    "Neck": (21, (0, 1, 2)), "Head": (24, (0, 1, 2)),
    "LeftUpperLeg": (27, (0, 1, 2)), "RightUpperLeg": (30, (0, 1, 2)),
    "LeftLowerLeg": (33, (0, 1, 2)), "RightLowerLeg": (36, (0, 1, 2)),
    "LeftFoot": (39, (0, 1, 2)), "RightFoot": (42, (0, 1, 2)),
    "LeftShoulder": (45, (0, 1, 2)), "RightShoulder": (48, (0, 1, 2)),
    "LeftUpperArm": (51, (0, 1, 2)), "RightUpperArm": (54, (0, 1, 2)),
    "LeftLowerArm": (57, (0, 1, 2)), "RightLowerArm": (60, (0, 1, 2)),
    "LeftHand": (63, (0, 1, 2)), "RightHand": (66, (0, 1, 2)),
    "LeftToes": (69, (2,)), "RightToes": (70, (2,)),
    "LeftEye": (71, (1, 2)), "RightEye": (73, (1, 2)), "Jaw": (75, (1, 2)),
}
_FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"]
for _side, _base in (("Left", 90), ("Right", 110)):
    for _i, _finger in enumerate(_FINGERS):
        _s = _base + 4 * _i
        _names = ("Metacarpal", "Proximal", "Distal") if _finger == "Thumb" else ("Proximal", "Intermediate", "Distal")
        BONE_SLOTS[f"{_side}{_finger}{_names[0]}"] = (_s, (1, 2))
        BONE_SLOTS[f"{_side}{_finger}{_names[1]}"] = (_s + 2, (2,))
        BONE_SLOTS[f"{_side}{_finger}{_names[2]}"] = (_s + 3, (2,))

HIPS_SLOTS = {"position_hi": (0, 1, 2), "position_lo": (3, 4, 5), "rotation_y": (6, 7, 8), "rotation_z": (9, 10, 11)}


def slot_grid_position(slot: int, layer: int = 0) -> Tuple[int, int]:
    """Slot index -> (slot column, row from the top). Layers are further avatars, 3 columns each."""
    col, row = divmod(slot, SLOT_ROWS)
    col += (layer >> 1) * 3
    if layer & 1:
        col = SLOT_COLS - 1 - col
    return col, row


def angles_to_slots(bone: str, angles_deg: Sequence[float]) -> Dict[int, float]:
    """Swing-twist angles (x, y, z degrees) of a bone -> {slot: value}. Hips is handled by encode_hips."""
    base, channels = BONE_SLOTS[bone]
    return {base + i: float(angles_deg[ch]) / 180.0 for i, ch in enumerate(channels)}


def slots_to_angles(bone: str, slots: np.ndarray) -> Tuple[float, float, float]:
    base, channels = BONE_SLOTS[bone]
    out = [0.0, 0.0, 0.0]
    for i, ch in enumerate(channels):
        out[ch] = float(slots[base + i]) * 180.0
    return tuple(out)


# -- hips ------------------------------------------------------------------------------------

def encode_hips(position: Sequence[float], rot_y: Sequence[float], rot_z: Sequence[float], scale: float) -> Dict[int, float]:
    """Hips slots: position/2 as hi+lo floats, rotation matrix y and z columns with |y|/|z| = scale."""
    out: Dict[int, float] = {}
    for axis in range(3):
        hi, lo = encode_float(float(position[axis]) / POSITION_SCALE)
        out[HIPS_SLOTS["position_hi"][axis]] = hi
        out[HIPS_SLOTS["position_lo"][axis]] = lo
    y = np.asarray(rot_y, dtype=float)
    z = np.asarray(rot_z, dtype=float)
    y = y / np.linalg.norm(y)
    z = z / np.linalg.norm(z)
    # Keep both inside [-1, 1]: the longer one has unit length.
    ly, lz = (1.0, 1.0 / scale) if scale >= 1.0 else (scale, 1.0)
    for axis in range(3):
        out[HIPS_SLOTS["rotation_y"][axis]] = float(y[axis] * ly)
        out[HIPS_SLOTS["rotation_z"][axis]] = float(z[axis] * lz)
    return out


def _orthogonalize(u: np.ndarray, v: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    b = float(np.dot(u, v)) * -2.0
    a = float(np.dot(u, u) + np.dot(v, v))
    a += math.sqrt(max(0.0, a * a - b * b))
    uu = u * a + v * b
    uu = uu * (np.dot(u, uu) / np.dot(uu, uu))
    vv = v * a + u * b
    vv = vv * (np.dot(v, vv) / np.dot(vv, vv))
    return uu, vv


def decode_hips(slots: np.ndarray) -> Dict[str, object]:
    """-> {"position": xyz, "rot_y": unit, "rot_z": unit, "scale": float, "error": float}."""
    pos = [decode_float(slots[HIPS_SLOTS["position_hi"][a]], slots[HIPS_SLOTS["position_lo"][a]]) * POSITION_SCALE for a in range(3)]
    ry = np.array([slots[i] for i in HIPS_SLOTS["rotation_y"]], dtype=float)
    rz = np.array([slots[i] for i in HIPS_SLOTS["rotation_z"]], dtype=float)
    oy, oz = _orthogonalize(ry, rz)
    ly, lz = float(np.linalg.norm(oy)), float(np.linalg.norm(oz))
    err = float(np.sum((ry - oy) ** 2) + np.sum((rz - oz) ** 2))
    return {"position": np.array(pos), "rot_y": oy / ly, "rot_z": oz / lz, "scale": ly / lz, "error": math.sqrt(err)}


# -- images -------------------------------------------------------------------------------------

def encode_frame(slot_values: Dict[int, float], width: int = 1280, height: int = 720, layer: int = 0,
                 image: Optional[np.ndarray] = None) -> np.ndarray:
    """Draw slots into an RGB uint8 image (H, W, 3). Unused squares stay black."""
    if image is None:
        image = np.zeros((height, width, 3), dtype=np.uint8)
    height, width = image.shape[:2]
    sq_w, sq_h = width / GRID_W, height / GRID_H
    for slot, value in slot_values.items():
        col, row = slot_grid_position(slot, layer)
        colors = encode_snorm(value)
        if layer & 1:
            colors = colors[::-1]
        for k, c in enumerate(colors):
            x0, x1 = int(round((col * TILE_LEN + k) * sq_w)), int(round((col * TILE_LEN + k + 1) * sq_w))
            y0, y1 = int(round(row * sq_h)), int(round((row + 1) * sq_h))
            image[y0:y1, x0:x1] = np.clip(np.round(np.array(c) * 255.0), 0, 255).astype(np.uint8)
    return image


def decode_frame(image: np.ndarray, layer: int = 0, slots: Optional[Iterable[int]] = None, window: float = 0.5,
                 grid_w: int = GRID_W, grid_h: int = GRID_H) -> np.ndarray:
    """RGB image (H, W, 3..4; uint8 or float) -> array of SLOT_COLS*SLOT_ROWS slot values.

    Each square is sampled as the mean of its central ``window`` fraction, which tolerates
    scaling and compression bleed at the square borders. ``grid_w`` lets a cropped strip
    (for example the first 6 squares = one avatar) be decoded; slots outside it stay 0.
    """
    img = np.asarray(image)
    if img.dtype == np.uint8:
        img = img.astype(np.float32) / 255.0
    height, width = img.shape[:2]
    sq_w, sq_h = width / grid_w, height / grid_h
    out = np.zeros(SLOT_COLS * SLOT_ROWS, dtype=np.float64)
    visible = (grid_w // TILE_LEN) * grid_h
    wanted = range(min(SLOT_COLS * SLOT_ROWS, visible)) if slots is None else slots
    m = (1.0 - window) / 2.0
    for slot in wanted:
        col, row = slot_grid_position(slot, layer)
        colors = []
        for k in range(TILE_LEN):
            gx = col * TILE_LEN + k
            x0, x1 = int((gx + m) * sq_w), max(int((gx + 1 - m) * sq_w), int((gx + m) * sq_w) + 1)
            y0, y1 = int((row + m) * sq_h), max(int((row + 1 - m) * sq_h), int((row + m) * sq_h) + 1)
            colors.append(img[y0:y1, x0:x1, :3].reshape(-1, 3).mean(axis=0))
        if layer & 1:
            colors = colors[::-1]
        out[slot] = decode_snorm(colors[0], colors[1])
    return out
