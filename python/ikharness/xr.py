"""Run the whole OpenXR chain unattended and score what appears on the screen.

    python -m ikharness.xr --dataset out/datasets/vsk_walk.json --tracker-set 6pt [--ik builtin]
        [--frames N] [--out-dir out/xr] [--eye 256] [--window 640x360] [--capture fbdir|import|ffmpeg]

Steps, each one a separate process, the way a closed application would be driven:

1. ``monado-service`` with the ikharness driver (null compositor, small eye buffers);
2. the Godot XR demo (``godot/harness/xr_demo.gd``) as an OpenXR application on a private
   Xvfb display with software OpenGL: head, hands and triggers through OpenXR, body trackers
   through the driver's state query, the ShaderMotion recorder on screen;
3. the calibration gesture: the reference rig's T-pose, a pause, both triggers;
4. the dataset's frames as tracker poses, one at a time; after each, wait until the screen
   stops changing, take the pixels off the screen, decode the ShaderMotion slots;
5. score the decoded poses against the reference (passed through the same pixel format).

Nothing here reads the application's memory or files except a status file used to know when
it is ready and calibrated; the poses come from the screen.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .calibration import run_calibration_gesture
from .dataset import Dataset
from .proc import Guard, ensure_headroom
from .protocol import Pose
from .negative import make_tracker_perturbation
from .replay import ROLE_TO_DEVICE, device_poses
from .scoring import score
from .screen import ScreenGrabber, save_png
from .service import MonadoService
from .trackers import TRACKER_SETS, place_trackers, rules_for

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "godot" / "harness"
GRID_W = 80


class XRDemo:
    """The Godot XR demo on its own Xvfb display, guarded, with the framebuffer exported."""

    def __init__(self, service: MonadoService, skeleton_path: Path, roles, ik: str = "builtin",
                 window: Tuple[int, int] = (640, 360), log_path: Optional[Path] = None, spectator: bool = True,
                 max_rss_mb: int = 2500):
        self.service = service
        self.skeleton_path = Path(skeleton_path)
        self.roles = list(roles)
        self.ik = ik
        self.window = window
        self.spectator = spectator
        self.log_path = Path(log_path) if log_path else ROOT / "out" / "xr-demo.log"
        self.max_rss_mb = max_rss_mb
        self.proc: Optional[subprocess.Popen] = None
        self.guard: Optional[Guard] = None
        self._tmp: Optional[tempfile.TemporaryDirectory] = None
        self._log = None
        self.fbdir: Optional[Path] = None
        self.status_path: Optional[Path] = None
        self.display_path: Optional[Path] = None

    def start(self) -> "XRDemo":
        ensure_headroom()
        self._tmp = tempfile.TemporaryDirectory(prefix="ikh-xr-")
        tmp = Path(self._tmp.name)
        self.fbdir = tmp / "fb"
        self.status_path = tmp / "status.json"
        self.display_path = tmp / "display"
        w, h = self.window
        godot = os.environ.get("GODOT", "godot")
        cmd = [sys.executable, "-m", "ikharness.xvfb", "--screen", f"{w}x{h}x24", "--fbdir", str(self.fbdir),
               "--display-file", str(self.display_path), "--",
               godot, "--path", str(HARNESS), "--display-driver", "x11", "--rendering-driver", "opengl3",
               "--audio-driver", "Dummy", "--xr-mode", "on", "--resolution", f"{w}x{h}", "--position", "0,0",
               "-s", "xr_demo.gd", "--",
               "--skeleton", str(self.skeleton_path.resolve()), "--ik", self.ik, "--roles", ",".join(self.roles),
               "--port", str(self.service.port), "--window", f"{w}x{h}", "--status", str(self.status_path),
               "--spectator", "1" if self.spectator else "0"]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "w")
        env = self.service.client_env({"LP_NUM_THREADS": os.environ.get("LP_NUM_THREADS", "1")})
        self.proc = subprocess.Popen(cmd, env=env, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
        self.guard = Guard(self.proc, max_rss_mb=self.max_rss_mb)
        return self

    @property
    def display(self) -> Optional[str]:
        try:
            return self.display_path.read_text().strip() or None
        except OSError:
            return None

    def status(self) -> dict:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {}

    def log_tail(self, n: int = 3000) -> str:
        try:
            return self.log_path.read_text()[-n:]
        except OSError:
            return ""

    def wait(self, predicate, what: str, timeout: float = 90.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                why = f" (stopped by the guard: {self.guard.killed})" if self.guard and self.guard.killed else ""
                raise RuntimeError(f"XR demo exited with {self.proc.returncode}{why} while waiting for {what}:\n{self.log_tail()}")
            st = self.status()
            if st and predicate(st):
                return st
            time.sleep(0.1)
        raise TimeoutError(f"XR demo: timed out waiting for {what}; status {self.status()}\n{self.log_tail()}")

    def grabber(self, capture: str = "fbdir") -> ScreenGrabber:
        if capture == "fbdir":
            return ScreenGrabber(fbdir=self.fbdir)
        return ScreenGrabber(display=self.display, tool=capture)

    def stop(self) -> None:
        if self.guard is not None:
            self.guard.stop()
        if self.proc is not None:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self) -> "XRDemo":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def strip_region(window: Tuple[int, int], columns: int = 3) -> Tuple[int, int, int, int]:
    """Screen rectangle holding ``columns`` ShaderMotion slot columns (one avatar uses 3)."""
    return (0, 0, int(round(columns * 2 * window[0] / GRID_W)), window[1])


def run(dataset_path: Path, tracker_set: str = "6pt", ik: str = "builtin", out_dir: Path = ROOT / "out" / "xr",
        frames: int = 0, eye: int = 256, window: Tuple[int, int] = (640, 360), capture: str = "fbdir",
        calibrate_hold: float = 1.0, min_dwell: float = 0.3, settle_timeout: float = 5.0, port: int = 4343,
        spectator: bool = True, perturb: Optional[str] = None, video: Optional[str] = None, video_fps: int = 20,
        video_hold: float = 0.2, log=print):
    """Returns (report through pixels, info dict). Frames are saved as ``frame_<index>.png``.

    ``perturb`` is ``name:magnitude[:seed]`` (see :mod:`ikharness.negative`), applied to the
    tracker poses before they enter the driver; the calibration frame gets index -1.

    ``video`` names a preset of :mod:`ikharness.video` (for example ``x264-crf23``): the
    demo's display is then also recorded with ``ffmpeg -f x11grab`` during the replay, every
    pose is held ``video_hold`` seconds longer, and the poses are decoded a second time from
    the recording (``info["video"]``), which shows what a stream of the screen would give.
    """
    from .shadermotion.readout import decode_image, roundtrip_dataset

    dataset_path = Path(dataset_path)
    dataset = Dataset.load(dataset_path)
    roles = TRACKER_SETS[tracker_set] if tracker_set in TRACKER_SETS else tracker_set.split(",")
    rules = rules_for(dataset.skeleton)
    count = len(dataset.frames) if frames <= 0 else min(frames, len(dataset.frames))
    hook = make_tracker_perturbation(perturb) if perturb else None
    tag = f"_{perturb.replace(':', '-')}" if perturb else ""
    stem = f"{dataset_path.stem}_{ik}_{tracker_set}{tag}"

    def poses_for(index: int):
        from .calibration import rest_frame
        frame = rest_frame(dataset.skeleton) if index < 0 else dataset.frames[index]
        trackers = place_trackers(frame, dataset.skeleton, roles, rules)
        return device_poses(hook(trackers, index) if hook else trackers)

    frame_dir = Path(out_dir) / f"{stem}.screen"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for old in frame_dir.glob("*.png"):
        old.unlink()
    body = [r for r in roles if r not in ROLE_TO_DEVICE]
    region = strip_region(window)
    info = {"dataset": str(dataset_path), "tracker_set": tracker_set, "ik": ik, "frames": count, "capture": capture,
            "window": list(window), "eye": eye, "perturbation": perturb or ""}

    with MonadoService(port=port, eye=(eye, eye), trackers=body or ["waist"],
                       log_path=Path(out_dir) / f"{stem}.monado.log") as service:
        with service.connect() as client, \
                XRDemo(service, dataset_path, roles, ik=ik, window=window, spectator=spectator,
                       log_path=Path(out_dir) / f"{stem}.demo.log") as demo:
            used = {ROLE_TO_DEVICE.get(r, r) for r in roles}
            unused = {d.name: Pose.disconnected() for d in client.devices if d.name not in used}
            tpose = poses_for(-1)
            tpose.update(unused)
            client.send_frame(tpose, frame_id=0)
            st = demo.wait(lambda s: s.get("state") == "ready" and s.get("profile", "").startswith("/interaction_profiles/")
                           and not s["profile"].endswith("/none"), "the OpenXR session and controllers")
            log(f"xr: demo ready, display {demo.display}, controllers {st['profile']}")
            want = set(roles)
            demo.wait(lambda s: want <= set(s.get("tracked", [])), f"tracking of {sorted(want)}")
            run_calibration_gesture(client, tpose, hold=calibrate_hold, log=log)
            st = demo.wait(lambda s: s.get("calibrations", 0) >= 1, "the T-pose calibration", timeout=30.0)
            info["calibration"] = {"root": st.get("root"), "height_ratio": st.get("height_ratio"), "roles": st.get("roles")}
            log(f"xr: calibrated, root {st.get('root')}, roles {','.join(st.get('roles', []))}")

            grabber = demo.grabber(capture)
            decoded: List[Optional[object]] = []
            unstable = 0
            previous = None
            recorder = None
            shown_at: List[float] = []
            video_path = Path(out_dir) / f"{stem}.{video}.mkv" if video else None
            if video:
                from .video import Recorder
                recorder = Recorder(demo.display, video_path, window, fps=video_fps, preset=video).start()
            started = time.monotonic()
            for i in range(count):
                poses = poses_for(i)
                poses.update(unused)
                client.send_frame(poses, frame_id=i + 1)
                time.sleep(min_dwell)
                image, stable = grabber.wait_stable(region=region, timeout=settle_timeout, differs_from=previous)
                unstable += 0 if stable else 1
                previous = image[region[1]:region[1] + region[3], region[0]:region[0] + region[2]].copy()
                save_png(image, frame_dir / f"frame_{i:05d}.png")
                decoded.append(decode_image(image, dataset.skeleton, time=dataset.frames[i].time))
                if recorder is not None:
                    shown_at.append(time.time() + video_hold / 2)
                    time.sleep(video_hold)
            seconds = time.monotonic() - started
            if recorder is not None:
                recorder.stop()
            info.update({"unstable_frames": unstable, "seconds_per_frame": seconds / max(count, 1), "grabs": grabber.grabs,
                         "demo_peak_rss_mb": demo.guard.peak_rss_mb, "service_peak_rss_mb": service.guard.peak_rss_mb,
                         "frame_dir": str(frame_dir)})

    reference = Dataset(skeleton=dataset.skeleton, frames=dataset.frames[:count], sources=dataset.sources,
                        generator=dataset.generator) if count < len(dataset.frames) else dataset
    via_pixels = score(roundtrip_dataset(reference, propagate_leftovers=False), decoded,
                       implementation=f"{ik}+openxr+screen", tracker_set=tracker_set)
    raw = score(reference, decoded, implementation=f"{ik}+openxr+screen(raw ref)", tracker_set=tracker_set)
    if video:
        from .shadermotion.readout import decode_directory
        from .video import compare_frames, extract_at, frame_times
        video_frames = Path(out_dir) / f"{stem}.{video}.frames"
        extract_at(video_path, shown_at, video_frames)
        from_video = decode_directory(video_frames, dataset.skeleton, count=count)
        video_report = score(roundtrip_dataset(reference, propagate_leftovers=False), from_video,
                             implementation=f"{ik}+openxr+video({video})", tracker_set=tracker_set)
        info["video"] = {"preset": video, "path": str(video_path), "bytes": video_path.stat().st_size,
                         "video_frames": len(frame_times(video_path)), "fps": video_fps,
                         "body_score_deg": video_report.body_score_deg, "weighted_score_deg": video_report.weighted_score_deg,
                         "difference_from_screen": compare_frames(decoded, from_video).to_dict(), "frame_dir": str(video_frames)}
    doc = via_pixels.to_dict()
    doc["readout"] = "openxr-screen"
    doc["raw_reference_weighted_deg"] = raw.weighted_score_deg
    doc["run"] = info
    (Path(out_dir) / f"{stem}.score.json").write_text(json.dumps(doc, indent=1))
    info["raw_reference_weighted_deg"] = raw.weighted_score_deg
    return via_pixels, info


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", required=True)
    p.add_argument("--tracker-set", default="6pt", help="name from trackers.TRACKER_SETS or a comma separated role list")
    p.add_argument("--ik", default="builtin", choices=["builtin", "renik", "none"])
    p.add_argument("--frames", type=int, default=0, help="only the first N frames")
    p.add_argument("--out-dir", default=str(ROOT / "out" / "xr"))
    p.add_argument("--eye", type=int, default=256, help="eye buffer size in pixels (small: software rendering)")
    p.add_argument("--window", default="640x360", help="desktop window = virtual screen size")
    p.add_argument("--capture", default="fbdir", choices=["fbdir", "import", "ffmpeg"],
                   help="fbdir: read the Xvfb framebuffer file; import/ffmpeg: screenshot the X display")
    p.add_argument("--calibrate-hold", type=float, default=1.0)
    p.add_argument("--min-dwell", type=float, default=0.3, help="seconds to wait after each frame before looking at the screen")
    p.add_argument("--port", type=int, default=4343)
    p.add_argument("--no-spectator", action="store_true", help="black window with only the ShaderMotion slots")
    p.add_argument("--perturb", default=None, help="name:magnitude[:seed] applied to the trackers, see ikharness.negative")
    p.add_argument("--video", default=None, metavar="PRESET",
                   help="also record the display with ffmpeg (preset from ikharness.video, e.g. x264-crf23) and score from the recording")
    p.add_argument("--video-fps", type=int, default=20)
    args = p.parse_args(argv)
    w, h = (int(v) for v in args.window.split("x"))
    report, info = run(Path(args.dataset), args.tracker_set, args.ik, Path(args.out_dir), args.frames, args.eye, (w, h),
                       args.capture, args.calibrate_hold, args.min_dwell, port=args.port, spectator=not args.no_spectator,
                       perturb=args.perturb, video=args.video, video_fps=args.video_fps)
    print(report.summary())
    print(f"xr: {info['frames']} frames off the screen ({info['capture']}), {info['seconds_per_frame']:.2f} s/frame, "
          f"{info['unstable_frames']} unstable; pixels vs raw reference {info['raw_reference_weighted_deg']:.2f} deg weighted; "
          f"peak memory demo {info['demo_peak_rss_mb']:.0f} MB, service {info['service_peak_rss_mb']:.0f} MB")
    print(f"xr: screenshots in {info['frame_dir']}")
    if "video" in info:
        v, d = info["video"], info["video"]["difference_from_screen"]
        print(f"xr: from the recording ({v['preset']}, {v['video_frames']} frames, {v['bytes'] / 1e6:.2f} MB): body score "
              f"{v['body_score_deg']:.2f} deg; poses differ from the screenshots by {d['mean_deg']:.3f} deg mean, "
              f"{d['max_deg']:.2f} max ({d['worst_bone']}); {v['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
