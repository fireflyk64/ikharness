"""Screen capture helpers: XWD parsing and the stability wait (no display needed)."""

import struct

import numpy as np
import pytest

from ikharness.screen import ScreenGrabber, crop, parse_xwd, same


def make_xwd(image: np.ndarray, byte_order: int = 0, ncolors: int = 2, name: bytes = b"xwdump\0\0") -> bytes:
    """A ZPixmap XWD file, 32 bits per pixel, masks as Xvfb writes them (BGRX in memory)."""
    h, w = image.shape[:2]
    header_size = 100 + len(name)
    fields = [header_size, 7, 2, 24, w, h, 0, byte_order, 32, byte_order, 32, 32, w * 4, 4,
              0xFF0000, 0x00FF00, 0x0000FF, 8, 256, ncolors, w, h, 0, 0, 0]
    value = (image[:, :, 0].astype(np.uint32) << 16) | (image[:, :, 1].astype(np.uint32) << 8) | image[:, :, 2]
    pixels = value.astype("<u4" if byte_order == 0 else ">u4").tobytes()
    return struct.pack(">25I", *fields) + name + b"\0" * (12 * ncolors) + pixels


@pytest.mark.parametrize("byte_order", [0, 1])
def test_parse_xwd_round_trip(byte_order):
    rng = np.random.default_rng(3)
    image = rng.integers(0, 256, size=(5, 7, 3), dtype=np.uint8)
    out = parse_xwd(make_xwd(image, byte_order))
    assert out.shape == (5, 7, 3) and out.dtype == np.uint8
    assert np.array_equal(out, image)


def test_parse_xwd_rejects_garbage():
    with pytest.raises(ValueError):
        parse_xwd(b"nope")
    with pytest.raises(ValueError):
        parse_xwd(make_xwd(np.zeros((4, 4, 3), np.uint8))[:-8])


def test_same_and_crop():
    a = np.zeros((4, 6, 3), np.uint8)
    b = a.copy()
    b[1, 5, 2] = 20
    assert same(a, a) and not same(a, b) and same(a, b, tolerance=20) and not same(a, b, tolerance=19)
    assert not same(a, a[:2])
    assert same(crop(a, (0, 0, 5, 4)), crop(b, (0, 0, 5, 4)))  # the difference is outside the region
    assert crop(a, None) is a


class FakeGrabber(ScreenGrabber):
    def __init__(self, images):
        super().__init__(fbdir="unused")
        self.images = list(images)

    def grab(self):
        self.grabs += 1
        return self.images.pop(0) if len(self.images) > 1 else self.images[0]


def frame(value, noise=0):
    img = np.full((2, 2, 3), value, np.uint8)
    img[0, 0, 0] += noise
    return img


def test_wait_stable_waits_for_change_then_for_rest():
    old = frame(10)
    # Still the old picture twice, then moving, then at rest with solver tremble below the tolerance.
    g = FakeGrabber([old, old, frame(80), frame(120), frame(200), frame(200, 9), frame(200)])
    image, stable = g.wait_stable(interval=0.0, differs_from=old, tolerance=16)
    assert stable and image[1, 1, 0] == 200
    assert g.grabs == 6


def test_wait_stable_gives_up():
    images = [frame(v) for v in range(0, 250, 40)] * 50
    g = FakeGrabber(images)
    image, stable = g.wait_stable(interval=0.001, timeout=0.05)
    assert not stable


def test_wait_stable_same_pose_as_before():
    old = frame(50)
    g = FakeGrabber([old])
    image, stable = g.wait_stable(interval=0.001, differs_from=old, change_timeout=0.02)
    assert stable and np.array_equal(image, old)
