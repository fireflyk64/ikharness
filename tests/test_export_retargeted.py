"""Godot as preprocessor: a dataset written as GLB must hold exactly the reference poses."""

import json
import os
import shutil
import struct
from pathlib import Path

import pytest

from ikharness.dataset import Dataset
from ikharness.export_retargeted import CLIP_NAME, export, verify
from ikharness.ikh import main as ikh_main

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def godot():
    exe = os.environ.get("GODOT") or shutil.which("godot") or str(Path.home() / ".local/bin/godot")
    if not Path(exe).exists():
        pytest.skip("Godot 4 binary not found (set GODOT)")
    os.environ["GODOT"] = exe
    return exe


def glb_json(path: Path) -> dict:
    data = path.read_bytes()
    assert data[:4] == b"glTF"
    return json.loads(data[20:20 + struct.unpack("<I", data[12:16])[0]])


def test_glb_holds_the_skeleton_and_one_key_per_frame(godot, tmp_path):
    dataset = Dataset.load(DATA / "mini_walk.json")
    export(DATA / "mini_walk.json", out=tmp_path / "clip.glb", fps=4.0, scene=tmp_path / "clip.tscn",
           animation=tmp_path / "clip.res")
    doc = glb_json(tmp_path / "clip.glb")
    names = {n.get("name") for n in doc["nodes"]}
    assert {"Hips", "LeftUpperArm", "RightFoot", "Head"} <= names
    joints = {doc["nodes"][j]["name"] for j in doc["skins"][0]["joints"]}
    assert set(dataset.skeleton.order) <= joints
    (anim,) = doc["animations"]
    assert anim["name"] == CLIP_NAME
    times = {doc["accessors"][s["input"]]["count"] for s in anim["samplers"]}
    assert times == {len(dataset.frames)}
    length = max(doc["accessors"][s["input"]]["max"][0] for s in anim["samplers"])
    assert length == pytest.approx((len(dataset.frames) - 1) / 4.0)
    assert (tmp_path / "clip.tscn").stat().st_size > 1000 and (tmp_path / "clip.res").stat().st_size > 1000


def test_round_trip_through_the_glb_is_exact(godot, tmp_path):
    export(DATA / "mini_walk.json", out=tmp_path / "clip.glb")
    report = verify(DATA / "mini_walk.json", tmp_path / "clip.glb")
    assert report.frames_scored == 3 and report.frames_missing == 0
    assert report.body_score_deg < 0.01
    assert report.end_effector_position_mean_m < 1e-4


def test_cli(godot, tmp_path, capsys):
    assert ikh_main(["dataset", "export-retargeted", "--dataset", str(DATA / "mini_walk.json"), "--out", str(tmp_path / "c.glb"),
                     "--scene", str(tmp_path / "c.tscn"), "--no-mesh", "--verify"]) == 0
    out = capsys.readouterr().out
    assert "verify: re-imported 3 frames, body score 0.00" in out
    # The scene has no body shapes; the GLB keeps them, its skeleton is the mesh's skin.
    assert "MeshInstance3D" not in (tmp_path / "c.tscn").read_text()
    assert glb_json(tmp_path / "c.glb")["skins"]
