"""Take the pixels off the screen, from outside the application that draws them.

Two sources:

* an Xvfb framebuffer file (``Xvfb -fbdir DIR`` keeps the screen in ``DIR/Xvfb_screen0`` in
  XWD format; :mod:`ikharness.xvfb` starts it that way with ``--fbdir``): no extra tools,
  a few milliseconds per grab;
* any X display, through ImageMagick ``import`` or ``ffmpeg -f x11grab``: slower, but works
  for a real desktop or a window owned by a closed application.

    grabber = ScreenGrabber(fbdir="/tmp/fb")          # or ScreenGrabber(display=":1")
    image = grabber.grab()                             # (H, W, 3) uint8
    image = grabber.wait_stable(region=(0, 0, 48, 360))

    python -m ikharness.screen (--fbdir DIR | --display :N) --out shot.png
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

Region = Tuple[int, int, int, int]  # x, y, width, height


def _mask_shift(mask: int) -> Tuple[int, int]:
    shift = (mask & -mask).bit_length() - 1 if mask else 0
    return shift, (mask >> shift).bit_length()


def parse_xwd(data: bytes) -> np.ndarray:
    """XWD (X Window Dump, ZPixmap, 24/32 bits per pixel) -> (H, W, 3) uint8 RGB."""
    if len(data) < 100:
        raise ValueError("not an XWD file (too short)")
    h = struct.unpack(">25I", data[:100])
    header_size, version, fmt, _depth, width, height = h[0:6]
    byte_order, bpp, bytes_per_line = h[7], h[11], h[12]
    red, green, blue, ncolors = h[14], h[15], h[16], h[19]
    if version != 7 or fmt != 2:
        raise ValueError(f"unsupported XWD file (version {version}, format {fmt})")
    if bpp not in (24, 32):
        raise ValueError(f"unsupported XWD depth: {bpp} bits per pixel")
    offset = header_size + ncolors * 12
    need = offset + bytes_per_line * height
    if len(data) < need:
        raise ValueError("truncated XWD file")
    rows = np.frombuffer(data, dtype=np.uint8, count=bytes_per_line * height, offset=offset).reshape(height, bytes_per_line)
    step = bpp // 8
    px = rows[:, : width * step].reshape(height, width, step).astype(np.uint32)
    order = range(step) if byte_order == 0 else range(step - 1, -1, -1)
    value = np.zeros((height, width), dtype=np.uint32)
    for i, b in enumerate(order):
        value |= px[:, :, b] << (8 * i)
    out = np.zeros((height, width, 3), dtype=np.uint8)
    for c, mask in enumerate((red, green, blue)):
        shift, bits = _mask_shift(mask)
        chan = (value & mask) >> shift
        out[:, :, c] = (chan << (8 - bits)) if bits < 8 else (chan >> (bits - 8))
    return out


def grab_fbdir(fbdir, screen: int = 0) -> np.ndarray:
    return parse_xwd(Path(fbdir, f"Xvfb_screen{screen}").read_bytes())


def grab_display(display: str, tool: Optional[str] = None, timeout: float = 20.0) -> np.ndarray:
    """Screenshot of the root window of an X display through ``import`` or ``ffmpeg``."""
    import io

    from PIL import Image
    tool = tool or ("import" if shutil.which("import") else "ffmpeg")
    if tool == "import":
        cmd = ["import", "-display", display, "-window", "root", "-silent", "png:-"]
    elif tool == "ffmpeg":
        cmd = ["ffmpeg", "-loglevel", "error", "-f", "x11grab", "-i", display, "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    else:
        raise ValueError(f"unknown screenshot tool {tool!r} (import, ffmpeg)")
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"{cmd[0]} is not installed")
    res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if res.returncode != 0 or not res.stdout:
        raise RuntimeError(f"{' '.join(cmd)} failed: {res.stderr.decode(errors='replace')[-500:]}")
    return np.array(Image.open(io.BytesIO(res.stdout)).convert("RGB"))


def crop(image: np.ndarray, region: Optional[Region]) -> np.ndarray:
    if region is None:
        return image
    x, y, w, h = region
    return image[y:y + h, x:x + w]


def same(a: np.ndarray, b: np.ndarray, tolerance: int = 0) -> bool:
    """True when two images have the same size and no channel differs by more than ``tolerance``."""
    if a.shape != b.shape:
        return False
    if tolerance <= 0:
        return bool(np.array_equal(a, b))
    return int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max(initial=0)) <= tolerance


class ScreenGrabber:
    def __init__(self, fbdir=None, display: Optional[str] = None, screen: int = 0, tool: Optional[str] = None):
        if fbdir is None and display is None:
            raise ValueError("ScreenGrabber needs fbdir= or display=")
        self.fbdir, self.display, self.screen, self.tool = fbdir, display, screen, tool
        self.grabs = 0

    def grab(self) -> np.ndarray:
        self.grabs += 1
        if self.fbdir is not None:
            return grab_fbdir(self.fbdir, self.screen)
        return grab_display(self.display, self.tool)

    def wait_stable(self, region: Optional[Region] = None, interval: float = 0.1, timeout: float = 5.0,
                    differs_from: Optional[np.ndarray] = None, change_timeout: float = 1.0,
                    tolerance: int = 32) -> Tuple[np.ndarray, bool]:
        """Grab until two consecutive grabs agree inside ``region``; returns (image, stable).

        Two grabs agree when no channel of any pixel differs by more than ``tolerance``
        (of 255): iterative IK solvers keep trembling by a few hundredths of a degree, which
        shows in the lowest ShaderMotion digit only (32/255 there is about 0.1 degrees).

        With ``differs_from`` (the region of the previous stable image) it first waits up to
        ``change_timeout`` seconds for the region to change at all, so a slow application is
        not read before it reacted. A pose identical to the previous one simply times out there.
        """
        start = time.monotonic()
        previous = self.grab()
        if differs_from is not None:
            while same(crop(previous, region), differs_from, tolerance) and time.monotonic() - start < change_timeout:
                time.sleep(interval)
                previous = self.grab()
        while True:
            time.sleep(interval)
            current = self.grab()
            if same(crop(current, region), crop(previous, region), tolerance):
                return current, True
            if time.monotonic() - start > timeout:
                return current, False
            previous = current


def save_png(image: np.ndarray, path) -> None:
    from PIL import Image
    Image.fromarray(image).save(path)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fbdir", help="directory given to Xvfb -fbdir")
    p.add_argument("--display", help="X display to screenshot, e.g. :1")
    p.add_argument("--tool", choices=["import", "ffmpeg"], help="screenshot tool for --display")
    p.add_argument("--out", required=True, help="PNG file to write")
    args = p.parse_args(argv)
    if not args.fbdir and not args.display:
        p.error("give --fbdir or --display")
    image = ScreenGrabber(fbdir=args.fbdir, display=args.display, tool=args.tool).grab()
    save_png(image, args.out)
    print(f"{args.out}: {image.shape[1]}x{image.shape[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
