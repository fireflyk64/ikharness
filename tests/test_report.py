"""ikh report: suite and score comparison tables (pure Python)."""

import json
from pathlib import Path

from ikharness.dataset import Dataset
from ikharness.ikh import main as ikh_main
from ikharness.negative import perturb_frames
from ikharness.report import build, markdown
from ikharness.scoring import score

DATA = Path(__file__).parent / "data"


def suite_doc(ik, final, entries, **extra):
    doc = {"suite": "t", "implementation": ik, "final_deg": final, "quality": 50.0, "seconds": 1.0,
           "entries": [{"dataset": d, "tracker_set": s, "weight": 1.0, "body_score_deg": v, "weighted_score_deg": v,
                        "end_effector_m": 0.01, "frames": 3} for d, s, v in entries]}
    doc.update(extra)
    return doc


def test_suite_table_with_deltas(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(suite_doc("builtin", 10.0, [("walk", "6pt", 9.0), ("walk", "11pt", 11.0)])))
    b.write_text(json.dumps(suite_doc("renik", 12.5, [("walk", "6pt", 13.0), ("dance", "6pt", 12.0)], readout="xr", calibration="tpose")))
    tables = build([a, b])
    assert len(tables) == 1 and tables[0]["kind"] == "suite"
    assert tables[0]["columns"] == ["walk 6pt", "walk 11pt", "dance 6pt"]
    assert tables[0]["rows"][1]["label"] == "renik [xr, tpose]" and tables[0]["rows"][1]["delta_final_deg"] == 2.5
    text = markdown(tables[0])
    assert "| renik [xr, tpose] | **12.50** (+2.50) | 50.0 | 13.00 (+4.00) |  | 12.00 |" in text
    assert "against `builtin`" in text


def test_score_table_names_the_bones_that_changed(tmp_path, capsys):
    dataset = Dataset.load(DATA / "mini_walk.json")
    good = score(dataset, list(dataset.frames), implementation="echo", tracker_set="6pt")
    bad = score(dataset, perturb_frames(dataset.frames, "rotate_arms:20"), implementation="bent", tracker_set="6pt")
    good.save(tmp_path / "good.score.json")
    bad.save(tmp_path / "bad.score.json")
    assert ikh_main(["report", str(tmp_path / "good.score.json"), str(tmp_path / "bad.score.json"),
                     "--out", str(tmp_path / "r.md"), "--json", str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    assert "| bone | good | bad |" in out and "**body score°**" in out
    assert "`bad` against `good`: worse on" in out and "UpperArm +20.0°" in out
    tables = json.loads((tmp_path / "r.json").read_text())
    assert tables[0]["kind"] == "score" and tables[0]["baseline"] == "good"
    legs = [b for b, _ in tables[0]["largest_changes"][0]["worse"] if "Leg" in b]
    assert not legs
    assert (tmp_path / "r.md").read_text().strip() == out.strip()


def test_mixed_inputs_give_two_tables(tmp_path):
    dataset = Dataset.load(DATA / "mini_walk.json")
    score(dataset, list(dataset.frames), implementation="echo").save(tmp_path / "x.score.json")
    (tmp_path / "s.json").write_text(json.dumps(suite_doc("none", 40.0, [("walk", "6pt", 40.0)])))
    tables = build([tmp_path / "x.score.json", tmp_path / "s.json"], baseline=None)
    assert [t["kind"] for t in tables] == ["suite", "score"]
    assert "(" not in markdown(tables[0])
