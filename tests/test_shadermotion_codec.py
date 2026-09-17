"""ShaderMotion reference codec: numbers <-> colors, hips float scheme, frames, a genuine frame."""

from pathlib import Path

import numpy as np
import pytest

from ikharness.shadermotion import codec as C

DATA = Path(__file__).parent / "data" / "shadermotion"


def quantize(c):
    return tuple(round(v * 255.0) / 255.0 for v in c)


def test_snorm_round_trip_exact_and_8bit():
    values = np.linspace(-1.0, 1.0, 4001)
    exact = max(abs(C.decode_snorm(*C.encode_snorm(v)) - v) for v in values)
    assert exact < 1e-9
    q = max(abs(C.decode_snorm(*(quantize(c) for c in C.encode_snorm(v))) - v) for v in values)
    assert q * 180.0 < 0.01  # degrees, after 8 bit color quantization


def test_gray_curve_is_continuous():
    values = np.linspace(-1.0, 1.0, 20001)
    colors = np.array([np.concatenate(C.encode_snorm(v)) for v in values])
    step = np.abs(np.diff(colors, axis=0)).sum(axis=1)
    # one channel moves at a time, by (dv * 364) / 2
    assert step.max() < (values[1] - values[0]) * C.HALF / 2 * 1.01 + 1e-12


def test_levels_use_pure_ternary_colors():
    for n in (0, 1, 364, 727, 728):
        c0, c1 = C.encode_snorm(n / C.HALF - 1.0)
        assert all(min(abs(ch - t) for t in (0.0, 0.5, 1.0)) < 1e-9 for ch in c0 + c1)
    assert C.encode_snorm(-1.0) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def test_wide_float_round_trip():
    values = np.concatenate([np.linspace(-3.0, 3.0, 6001), [0.999, 1.0, 1.0005, 1.0014, -1.0014, 2.0027, 729.0]])
    for v in values:
        hi, lo = C.encode_float(v)
        assert -1.0 <= hi <= 1.0 and -1.0 <= lo <= 1.0
        assert abs(C.decode_float(hi, lo) - v) < 1e-9, v
    # Sloppy decoders may ignore hi inside the normal range.
    for v in (-0.9, -0.2, 0.0, 0.5, 0.99):
        hi, lo = C.encode_float(v)
        assert abs(hi) < 1e-12 and abs(lo - v) < 1e-12


def test_wide_float_survives_color_codec():
    for v in (-2.345, -0.75, 0.0, 0.123456, 1.75):
        hi, lo = C.encode_float(v)
        hi2 = C.decode_snorm(*(quantize(c) for c in C.encode_snorm(hi)))
        lo2 = C.decode_snorm(*(quantize(c) for c in C.encode_snorm(lo)))
        assert abs(C.decode_float(hi2, lo2) - v) < 1e-4


def test_layout_matches_specification():
    assert C.BONE_SLOTS["Spine"] == (12, (0, 1, 2))
    assert C.BONE_SLOTS["LeftToes"] == (69, (2,)) and C.BONE_SLOTS["RightEye"] == (73, (1, 2))
    assert C.BONE_SLOTS["LeftThumbMetacarpal"][0] == 90 and C.BONE_SLOTS["LeftIndexProximal"][0] == 94
    assert C.BONE_SLOTS["RightLittleDistal"] == (129, (2,))
    assert C.slot_grid_position(0) == (0, 0) and C.slot_grid_position(44) == (0, 44) and C.slot_grid_position(45) == (1, 0)
    used = set()
    for bone, (base, ch) in C.BONE_SLOTS.items():
        n = 12 if bone == "Hips" else len(ch)
        span = set(range(base, base + n))
        assert not (used & span), bone
        used |= span
    assert max(used) == 129


@pytest.mark.parametrize("size", [(1280, 720), (320, 180), (1920, 1080)])
def test_frame_round_trip(size):
    rng = np.random.default_rng(3)
    slots = {}
    angles = {}
    for bone in ("Spine", "Head", "LeftUpperArm", "RightLowerLeg", "LeftToes", "RightIndexProximal"):
        a = rng.uniform(-170, 170, size=3)
        angles[bone] = a
        slots.update(C.angles_to_slots(bone, a))
    rot = np.array([[0.36, 0.48, -0.8], [-0.8, 0.6, 0.0], [0.48, 0.64, 0.6]]).T  # orthonormal columns
    slots.update(C.encode_hips((0.25, 0.93, -1.4), rot[:, 1], rot[:, 2], scale=0.85))
    img = C.encode_frame(slots, *size)
    assert img.shape == (size[1], size[0], 3) and img.dtype == np.uint8
    dec = C.decode_frame(img)
    for bone, a in angles.items():
        got = C.slots_to_angles(bone, dec)
        for ch in C.BONE_SLOTS[bone][1]:
            assert abs(got[ch] - a[ch]) < 0.01, (bone, ch)
    hips = C.decode_hips(dec)
    assert np.allclose(hips["position"], (0.25, 0.93, -1.4), atol=2e-4)
    assert np.allclose(hips["rot_y"], rot[:, 1], atol=2e-3) and np.allclose(hips["rot_z"], rot[:, 2], atol=2e-3)
    assert abs(hips["scale"] - 0.85) < 2e-3 and hips["error"] < 0.01


def test_second_layer_is_mirrored():
    slots = C.angles_to_slots("Head", (10.0, -20.0, 30.0))
    img = C.encode_frame(slots, 1280, 720, layer=1)
    assert img[:, :640].max() == 0 and img[:, 640:].max() > 0  # drawn on the right side
    got = C.slots_to_angles("Head", C.decode_frame(img, layer=1))
    assert np.allclose(got, (10.0, -20.0, 30.0), atol=0.01)


def test_decodes_a_genuine_shadermotion_frame():
    """Frame from the original Unity encoder: the hips rotation columns must be orthogonal and sane."""
    from PIL import Image
    img = np.array(Image.open(DATA / "upstream_frame.png").convert("RGB"))
    dec = C.decode_frame(img, grid_w=6)
    assert np.all(np.abs(dec[:130]) <= 1.0 + 1e-6)
    hips = C.decode_hips(dec)
    assert hips["error"] < 0.05, hips
    assert abs(float(np.dot(hips["rot_y"], hips["rot_z"]))) < 1e-6
    assert 0.3 < hips["scale"] < 2.0, hips["scale"]
    assert np.all(np.abs(hips["position"]) < 5.0)
    # A humanoid in a plausible pose: spine angles are small, most bones are within ±120 degrees.
    for bone in ("Spine", "Chest", "Neck"):
        assert max(abs(a) for a in C.slots_to_angles(bone, dec)) < 60.0, bone
