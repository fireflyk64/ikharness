"""ikh subcommands that need no Godot: score, shadermotion encode/decode, dataset info."""

import json
from pathlib import Path

from ikharness.ikh import main

DATA = Path(__file__).parent / "data"


def test_shadermotion_encode_decode_score_round_trip(tmp_path, capsys):
    assert main(["shadermotion", "encode", "--dataset", str(DATA / "mini_walk.json"), "--out-dir", str(tmp_path / "png")]) == 0
    assert len(list((tmp_path / "png").glob("frame_*.png"))) == 3
    assert main(["shadermotion", "decode", "--skeleton", str(DATA / "mini_walk.json"), "--images", str(tmp_path / "png"),
                 "--out", str(tmp_path / "r.json"), "--implementation", "rt"]) == 0
    doc = json.loads((tmp_path / "r.json").read_text())
    assert doc["format"] == "ikharness-result/1" and len(doc["frames"]) == 3 and doc["skeleton"]["bones"]
    assert main(["score", "--dataset", str(DATA / "mini_walk.json"), "--result", str(tmp_path / "r.json"),
                 "--through-shadermotion", "--out", str(tmp_path / "s.json")]) == 0
    out = capsys.readouterr().out
    assert "body score 0.00 deg" in out
    assert json.loads((tmp_path / "s.json").read_text())["body_score_deg"] < 1e-4
    assert main(["score", "--dataset", str(DATA / "mini_walk.json"), "--result", str(tmp_path / "r.json")]) == 0
    assert "body score 1." in capsys.readouterr().out  # the format's floor without cancellation


def test_dataset_info_reports_conformance(capsys):
    assert main(["dataset", "info", str(DATA / "mini_walk.json")]) == 0
    out = capsys.readouterr().out
    assert "rest vs humanoid profile: max 0.0" in out and "limb lengths" in out
