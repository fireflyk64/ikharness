"""ShaderMotion through video: encode frame sequences, record a display, read frames back.

Three uses:

* :func:`roundtrip` / ``ikh video roundtrip``: take lossless ShaderMotion frames (for example the
  screenshots of ``ikh xr``), push them through a video codec and measure what the codec did
  to the decoded poses. Deterministic, no display needed.
* :class:`Recorder`: record an X display with ``ffmpeg -f x11grab`` while something else
  drives the application (``ikh xr --video``); frame timestamps are wall-clock, so poses
  can be paired with the moments they were on screen.
* :func:`extract_at` / ``ikh video extract``: pull the frames nearest to given wall-clock
  times (or every Nth frame) out of a recording as PNGs for ``ikh shadermotion decode``.

    ikh video roundtrip --images out/xr/vsk_walk_builtin_6pt.screen --skeleton out/datasets/vsk_walk.json
    ikh video extract --video rec.mkv --out-dir frames [--every 5]
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from .mathutil import quat_angle
from .proc import Guard

#: name -> (container suffix, ffmpeg output options). Quality from near-lossless to streaming grade.
PRESETS: Dict[str, tuple] = {
    "ffv1": (".mkv", ["-c:v", "ffv1"]),
    "x264-crf0-444": (".mkv", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "0", "-pix_fmt", "yuv444p"]),
    "x264-crf18-444": (".mkv", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv444p"]),
    "x264-crf18": (".mp4", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]),
    "x264-crf23": (".mp4", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]),
    "x264-crf28": (".mp4", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p"]),
    "x264-crf35": (".mp4", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "35", "-pix_fmt", "yuv420p"]),
    "vp9-crf32": (".webm", ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-deadline", "realtime", "-cpu-used", "8",
                            "-pix_fmt", "yuv420p"]),
    "mjpeg-q5": (".mkv", ["-c:v", "mjpeg", "-q:v", "5", "-pix_fmt", "yuvj420p"]),
    "theora-q7": (".ogv", ["-c:v", "libtheora", "-q:v", "7", "-pix_fmt", "yuv420p"]),
}
DEFAULT_PRESETS = ["ffv1", "x264-crf18-444", "x264-crf18", "x264-crf23", "x264-crf28", "x264-crf35", "vp9-crf32", "mjpeg-q5"]
#: Full-range BT.709 both ways, so a codec's color conversion is the only loss on top of quantization.
TO_YUV = "scale=in_range=full:out_range=full:out_color_matrix=bt709:flags=neighbor"
TO_RGB = "scale=in_range=full:in_color_matrix=bt709:out_range=full:flags=neighbor,format=rgb24"


def _ffmpeg(args: Sequence[str], timeout: float = 600.0) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    res = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
                         capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg {' '.join(args)} failed:\n{res.stderr[-2000:]}")


def encode_images(image_dir, out_path, options: Sequence[str], fps: int = 30, hold: int = 1) -> Path:
    """``frame_%05d.png`` (numbered from 0, no gaps) -> video; every image is shown ``hold`` frames."""
    vf = TO_YUV if hold <= 1 else f"{TO_YUV},fps={fps * hold}"
    _ffmpeg(["-framerate", str(fps), "-start_number", "0", "-i", str(Path(image_dir) / "frame_%05d.png"),
             "-vf", vf, "-color_range", "pc", "-colorspace", "bt709", *options, "-threads", "2", str(out_path)])
    return Path(out_path)


def frame_times(video) -> List[float]:
    """Presentation time of every video frame (seconds; wall-clock epoch for :class:`Recorder` files)."""
    res = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time",
                          "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr[-1000:]}")
    return [float(line.split(",")[0]) for line in res.stdout.split() if line.strip(",")]


def extract_indices(video, indices: Sequence[int], out_dir, full_range: bool = True) -> List[Path]:
    """Video frames number ``indices[k]`` -> ``out_dir/frame_<k>.png``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.png"):
        old.unlink()
    order = sorted(set(indices))
    with tempfile.TemporaryDirectory(prefix="ikh-video-") as tmp:
        select = "+".join(f"eq(n\\,{n})" for n in order)
        convert = TO_RGB if full_range else "format=rgb24"
        _ffmpeg(["-copyts", "-i", str(video), "-vf", f"select='{select}',{convert}", "-fps_mode", "passthrough",
                 "-start_number", "0", str(Path(tmp) / "sel_%05d.png")])
        out = []
        for k, n in enumerate(indices):
            src = Path(tmp) / f"sel_{order.index(n):05d}.png"
            if not src.exists():
                raise RuntimeError(f"{video}: frame {n} could not be extracted")
            dst = out_dir / f"frame_{k:05d}.png"
            shutil.copyfile(src, dst)
            out.append(dst)
    return out


def extract_at(video, times: Sequence[float], out_dir, full_range: bool = True) -> List[int]:
    """Frames nearest to ``times`` (same clock as the video's timestamps) -> PNGs; returns their indices."""
    pts = np.asarray(frame_times(video))
    if pts.size == 0:
        raise RuntimeError(f"{video}: no video frames")
    indices = [int(np.abs(pts - t).argmin()) for t in times]
    extract_indices(video, indices, out_dir, full_range)
    return indices


class Recorder:
    """``ffmpeg -f x11grab`` of a whole X display, stamped with wall-clock time, guarded."""

    def __init__(self, display: str, out_path, size, fps: int = 20, preset: str = "x264-crf18", log_path=None,
                 max_rss_mb: int = 600):
        self.display, self.out_path, self.size, self.fps, self.preset = display, Path(out_path), size, fps, preset
        self.log_path = Path(log_path) if log_path else self.out_path.with_suffix(".ffmpeg.log")
        self.max_rss_mb = max_rss_mb
        self.proc: Optional[subprocess.Popen] = None
        self.guard: Optional[Guard] = None
        self._log = None

    def start(self) -> "Recorder":
        _, options = PRESETS[self.preset]
        # Matroska keeps the wall-clock timestamps and stays readable when ffmpeg is stopped.
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y", "-f", "x11grab", "-draw_mouse", "0",
               "-use_wallclock_as_timestamps", "1", "-framerate", str(self.fps), "-video_size", f"{self.size[0]}x{self.size[1]}",
               "-i", self.display, "-vf", TO_YUV, "-color_range", "pc", "-colorspace", "bt709", *options, "-threads", "1",
               "-copyts", "-f", "matroska", str(self.out_path)]
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
        self.guard = Guard(self.proc, max_rss_mb=self.max_rss_mb)
        time.sleep(0.5)
        if self.proc.poll() is not None:
            self._log.close()
            raise RuntimeError(f"ffmpeg x11grab exited at once:\n{self.log_path.read_text()[-1500:]}")
        return self

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)   # lets ffmpeg finish the file
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
        if self.guard is not None:
            self.guard.stop()
            self.guard = None
        if self.proc is not None:
            self.proc.wait()
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self) -> "Recorder":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


@dataclass
class PoseDifference:
    """How far poses decoded from processed frames are from poses decoded from the originals."""

    mean_deg: float
    p95_deg: float
    max_deg: float
    worst_bone: str
    hips_position_max_m: float
    frames: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def compare_frames(reference: Sequence, other: Sequence) -> PoseDifference:
    """Global rotation differences over all bones and frames of two decoded frame lists."""
    angles, worst, worst_bone, hips = [], 0.0, "", 0.0
    for a, b in zip(reference, other):
        for bone, ta in a.bones.items():
            if bone not in b.bones:
                continue
            ang = float(np.degrees(quat_angle(ta.rotation, b.bones[bone].rotation)))
            angles.append(ang)
            if ang > worst:
                worst, worst_bone = ang, bone
        if "Hips" in a.bones and "Hips" in b.bones:
            hips = max(hips, float(np.linalg.norm(a.bones["Hips"].position - b.bones["Hips"].position)))
    arr = np.asarray(angles) if angles else np.zeros(1)
    return PoseDifference(float(arr.mean()), float(np.percentile(arr, 95)), float(arr.max()), worst_bone, hips,
                          min(len(reference), len(other)))


def roundtrip(image_dir, skeleton, presets: Sequence[str] = DEFAULT_PRESETS, fps: int = 30, hold: int = 3,
              keep_dir=None, dataset=None) -> List[dict]:
    """Encode ``image_dir`` with each preset, decode, and compare poses with the lossless frames.

    ``hold`` video frames show each pose (a stream holds poses for several frames; the middle
    one is read back). With ``dataset`` the rows also carry the score against the reference.
    """
    from .scoring import score
    from .shadermotion.readout import decode_directory, roundtrip_dataset

    image_dir = Path(image_dir)
    count = len(list(image_dir.glob("frame_*.png")))
    if count == 0:
        raise FileNotFoundError(f"no frame_*.png in {image_dir}")
    original = decode_directory(image_dir, skeleton, count=count)
    size = sum(f.stat().st_size for f in image_dir.glob("frame_*.png"))
    reference = roundtrip_dataset(dataset, propagate_leftovers=False) if dataset is not None else None

    def scored(frames):
        return score(reference, frames, implementation="video").body_score_deg if reference is not None else None

    rows = [{"preset": "png (lossless)", "bytes": size, "bytes_per_pose": size / count, "difference": compare_frames(original, original).to_dict(),
             "body_score_deg": scored(original)}]
    with tempfile.TemporaryDirectory(prefix="ikh-video-") as tmp:
        work = Path(keep_dir) if keep_dir else Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        for name in presets:
            suffix, options = PRESETS[name]
            video = work / f"{name}{suffix}"
            encode_images(image_dir, video, options, fps=fps, hold=hold)
            frames_dir = work / f"{name}.frames"
            extract_indices(video, [i * hold + hold // 2 for i in range(count)], frames_dir)
            decoded = decode_directory(frames_dir, skeleton, count=count)
            rows.append({"preset": name, "bytes": video.stat().st_size, "bytes_per_pose": video.stat().st_size / count,
                         "difference": compare_frames(original, decoded).to_dict(), "body_score_deg": scored(decoded)})
            if not keep_dir:
                shutil.rmtree(frames_dir)
                video.unlink()
    return rows


def format_rows(rows: Sequence[dict]) -> str:
    lines = [f"{'codec':<18}{'kB/pose':>9}{'mean deg':>10}{'p95 deg':>9}{'max deg':>9}  {'worst bone':<18}{'hips mm':>8}{'score':>8}"]
    for r in rows:
        d = r["difference"]
        s = "" if r.get("body_score_deg") is None else f"{r['body_score_deg']:8.2f}"
        lines.append(f"{r['preset']:<18}{r['bytes_per_pose'] / 1000:9.1f}{d['mean_deg']:10.3f}{d['p95_deg']:9.3f}{d['max_deg']:9.2f}  "
                     f"{d['worst_bone']:<18}{d['hips_position_max_m'] * 1000:8.1f}{s}")
    return "\n".join(lines)


def main(argv=None) -> int:
    from .dataset import Dataset

    p = argparse.ArgumentParser(prog="ikh video", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roundtrip", help="lossless frames -> codec -> frames; report what the codec did to the poses")
    r.add_argument("--images", required=True, help="directory of frame_<index>.png (ShaderMotion frames, e.g. from ikh xr)")
    r.add_argument("--skeleton", required=True, help="dataset JSON whose skeleton the poses are rebuilt on")
    r.add_argument("--presets", default=",".join(DEFAULT_PRESETS), help=f"comma separated, from: {', '.join(PRESETS)}")
    r.add_argument("--fps", type=int, default=30)
    r.add_argument("--hold", type=int, default=3, help="video frames per pose")
    r.add_argument("--keep", default=None, help="keep the videos and extracted frames in this directory")
    r.add_argument("--score", action="store_true", help="also score every variant against the dataset given as --skeleton")
    r.add_argument("--out", default=None, help="write the table as JSON")
    e = sub.add_parser("extract", help="video -> frame_<k>.png, every Nth frame or the frames nearest to given times")
    e.add_argument("--video", required=True)
    e.add_argument("--out-dir", required=True)
    e.add_argument("--every", type=int, default=1)
    e.add_argument("--offset", type=int, default=0, help="first frame to take with --every")
    e.add_argument("--times", default=None, help="JSON file with a list of timestamps (video clock) instead of --every")
    args = p.parse_args(argv)
    if args.cmd == "roundtrip":
        dataset = Dataset.load(args.skeleton)
        rows = roundtrip(args.images, dataset.skeleton, [s for s in args.presets.split(",") if s], args.fps, args.hold, args.keep,
                         dataset if args.score else None)
        print(format_rows(rows))
        if args.out:
            Path(args.out).write_text(json.dumps(rows, indent=1))
        return 0
    if args.times:
        indices = extract_at(args.video, json.loads(Path(args.times).read_text()), args.out_dir)
    else:
        total = len(frame_times(args.video))
        indices = list(range(args.offset, total, max(1, args.every)))
        extract_indices(args.video, indices, args.out_dir)
    print(f"{len(indices)} frame(s) from {args.video} to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
