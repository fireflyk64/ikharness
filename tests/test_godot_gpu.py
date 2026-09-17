"""The ShaderMotion recorder mesh + shader, rendered with software OpenGL under Xvfb."""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from ikharness.dataset import Dataset
from ikharness.run_godot import evaluate
from ikharness.shadermotion import codec as C
from ikharness.shadermotion import humanoid as H
from ikharness.shadermotion.readout import load_image
from ikharness.testfile import HarnessResult

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def godot():
    exe = os.environ.get("GODOT") or shutil.which("godot") or str(Path.home() / ".local/bin/godot")
    if not Path(exe).exists():
        pytest.skip("Godot 4 binary not found (set GODOT)")
    if not shutil.which("xvfb-run") and "IKH_GPU_LAUNCHER" not in os.environ:
        pytest.skip("xvfb-run not available for software rendering")
    os.environ["GODOT"] = exe
    return exe


def test_shader_matches_reference_encoder(godot, tmp_path):
    report, _ = evaluate(DATA / "mini_walk.json", "6pt", ik="builtin", settle=6, out_dir=tmp_path, readout="shadermotion-gpu")
    assert report.frames_scored == 3 and report.frames_missing == 0
    dataset = Dataset.load(DATA / "mini_walk.json")
    solved = HarnessResult.load(tmp_path / "mini_walk_builtin_6pt.result.json")
    for i in range(3):
        gpu = C.decode_frame(load_image(tmp_path / "mini_walk_builtin_6pt.shadermotion-gpu" / f"frame_{i:05d}.png"))
        expect, _ = H.frame_to_slots(solved.frames[i], dataset.skeleton, propagate_leftovers=False)
        for bone, (base, channels) in C.BONE_SLOTS.items():
            if bone == "Hips" or not dataset.skeleton.has(bone):
                continue
            for k in range(len(channels)):
                if base + k in expect:
                    diff = abs((gpu[base + k] - expect[base + k] + 1.0) % 2.0 - 1.0) * 180.0
                    assert diff < 0.1, (i, bone, channels[k], diff)
        hips = C.decode_hips(gpu)
        want = C.decode_hips(np.array([expect.get(s, 0.0) for s in range(C.SLOT_COLS * C.SLOT_ROWS)]))
        assert np.allclose(hips["position"], want["position"], atol=1e-3)
        assert np.allclose(hips["rot_y"], want["rot_y"], atol=1e-3) and np.allclose(hips["rot_z"], want["rot_z"], atol=1e-3)
        assert abs(hips["scale"] - want["scale"]) < 1e-3
