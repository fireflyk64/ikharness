"""``ikh shadermotion`` subcommands: encode reference frames to images, decode images to results.

    ikh shadermotion encode --dataset D --out-dir DIR [--width 640 --height 360]
    ikh shadermotion decode --skeleton D --images DIR_OR_FILE... --out result.json [--layer 0] [--grid-w 80]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..dataset import Dataset
from ..testfile import RESULT_FORMAT
from . import codec, humanoid
from .readout import decode_image, load_image


def cmd_encode(args) -> int:
    from PIL import Image
    d = Dataset.load(args.dataset)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(d.frames):
        slots, _ = humanoid.frame_to_slots(f, d.skeleton, propagate_leftovers=not args.no_leftovers)
        Image.fromarray(codec.encode_frame(slots, args.width, args.height, layer=args.layer)).save(out / f"frame_{i:05d}.png")
    print(f"wrote {len(d.frames)} frames to {out}")
    return 0


def cmd_decode(args) -> int:
    d = Dataset.load(args.skeleton)
    files = []
    for item in args.images:
        p = Path(item)
        files += sorted(p.glob("*.png")) if p.is_dir() else [p]
    frames = []
    for i, f in enumerate(files):
        m = re.search(r"(\d+)\.png$", f.name)
        index = int(m.group(1)) if m and not args.sequential else i
        frame = decode_image(load_image(f), d.skeleton, layer=args.layer, grid_w=args.grid_w)
        frames.append({"index": index, "time": 0.0, "bones": frame.to_dict()["bones"]})
    doc = {"format": RESULT_FORMAT, "implementation": args.implementation, "tracker_set": "", "readout": "shadermotion",
           "skeleton": {"bones": [{"name": b.name, "rest_global": dict(zip(("position", "rotation"), b.rest_global.as_lists()))}
                                  for b in (d.skeleton.bones[n] for n in d.skeleton.order)]},
           "frames": sorted(frames, key=lambda x: x["index"])}
    Path(args.out).write_text(json.dumps(doc))
    print(f"decoded {len(frames)} image(s) to {args.out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ikh shadermotion", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode", help="reference frames -> ShaderMotion PNGs (to test decoders and capture chains)")
    e.add_argument("--dataset", required=True)
    e.add_argument("--out-dir", required=True)
    e.add_argument("--width", type=int, default=640)
    e.add_argument("--height", type=int, default=360)
    e.add_argument("--layer", type=int, default=0)
    e.add_argument("--no-leftovers", action="store_true", help="encode like a shader: no twist hand-over between bones")
    e.set_defaults(fn=cmd_encode)
    dd = sub.add_parser("decode", help="ShaderMotion image(s) -> ikharness result file, on a dataset's skeleton")
    dd.add_argument("--skeleton", required=True, help="dataset JSON whose skeleton (proportions) the poses are rebuilt on")
    dd.add_argument("--images", nargs="+", required=True, help="PNG files or directories of frame_<index>.png")
    dd.add_argument("--out", required=True)
    dd.add_argument("--layer", type=int, default=0)
    dd.add_argument("--grid-w", type=int, default=codec.GRID_W, help="squares across the image (6 for a one-avatar crop)")
    dd.add_argument("--sequential", action="store_true", help="number frames by order instead of the digits in the file name")
    dd.add_argument("--implementation", default="external")
    dd.set_defaults(fn=cmd_decode)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
