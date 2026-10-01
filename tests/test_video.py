"""ShaderMotion frames through video codecs (needs ffmpeg; no display)."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from ikharness.dataset import Dataset
from ikharness.ikh import main as ikh_main
from ikharness.shadermotion.readout import load_image
from ikharness.video import PRESETS, encode_images, extract_at, extract_indices, format_rows, frame_times, roundtrip

DATA = Path(__file__).parent / "data"

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg and ffprobe")


@pytest.fixture(scope="module")
def frames(tmp_path_factory):
    out = tmp_path_factory.mktemp("frames")
    assert ikh_main(["shadermotion", "encode", "--dataset", str(DATA / "mini_walk.json"), "--out-dir", str(out), "--no-leftovers"]) == 0
    return out


def test_lossless_codec_returns_the_same_pixels(frames, tmp_path):
    video = encode_images(frames, tmp_path / "v.mkv", PRESETS["ffv1"][1], fps=30, hold=3)
    assert len(frame_times(video)) == 9
    extract_indices(video, [1, 4, 7], tmp_path / "out")
    for i in range(3):
        assert np.array_equal(load_image(tmp_path / "out" / f"frame_{i:05d}.png"), load_image(frames / f"frame_{i:05d}.png"))


def test_extract_at_picks_the_nearest_frames(frames, tmp_path):
    video = encode_images(frames, tmp_path / "v.mkv", PRESETS["ffv1"][1], fps=10, hold=1)
    times = frame_times(video)
    assert len(times) == 3 and times[1] - times[0] == pytest.approx(0.1, abs=1e-3)
    assert extract_at(video, [times[2] + 0.01, times[0] - 5.0], tmp_path / "out") == [2, 0]
    assert np.array_equal(load_image(tmp_path / "out" / "frame_00000.png"), load_image(frames / "frame_00002.png"))


def test_codec_loss_is_small_and_ordered(frames):
    dataset = Dataset.load(DATA / "mini_walk.json")
    rows = roundtrip(frames, dataset.skeleton, ["ffv1", "x264-crf18", "x264-crf35"], dataset=dataset)
    by = {r["preset"]: r for r in rows}
    assert by["ffv1"]["difference"]["max_deg"] < 1e-4   # same pixels; acos noise only
    assert 0.0 < by["x264-crf18"]["difference"]["mean_deg"] < by["x264-crf35"]["difference"]["mean_deg"] < 1.0
    assert by["x264-crf35"]["difference"]["max_deg"] < 3.0
    assert by["x264-crf35"]["bytes"] < by["x264-crf18"]["bytes"] < by["ffv1"]["bytes"]
    # The encoder side has no IK error here, so the score is the codec's damage alone.
    assert by["png (lossless)"]["body_score_deg"] < 0.01 and by["x264-crf35"]["body_score_deg"] < 1.0
    assert "x264-crf35" in format_rows(rows)


def test_cli_roundtrip_and_extract(frames, tmp_path, capsys):
    assert ikh_main(["video", "roundtrip", "--images", str(frames), "--skeleton", str(DATA / "mini_walk.json"),
                     "--presets", "mjpeg-q5", "--keep", str(tmp_path / "keep"), "--out", str(tmp_path / "t.json")]) == 0
    assert "mjpeg-q5" in capsys.readouterr().out and (tmp_path / "t.json").exists()
    assert ikh_main(["video", "extract", "--video", str(tmp_path / "keep" / "mjpeg-q5.mkv"), "--out-dir", str(tmp_path / "x"),
                     "--every", "3", "--offset", "1"]) == 0
    assert len(list((tmp_path / "x").glob("frame_*.png"))) == 3
