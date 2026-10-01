"""Compare runs: suites against each other, or score files bone by bone.

    ikh report                                   # every suite report under out/suite
    ikh report out/suite/default_renik.json out/suite/default_builtin.json [--baseline 0]
    ikh report A.score.json B.score.json         # per-bone table, deltas against the first
    ikh report ... --out report.md --json report.json

Suite reports come from ``ikh suite`` (one per implementation / readout / calibration);
score files from ``ikh eval``, ``ikh xr`` and ``ikh score``. Files of the two kinds can be
mixed on one command line; each kind gets its own table. Lower is better everywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]


def load(path) -> dict:
    doc = json.loads(Path(path).read_text())
    doc["_path"] = str(path)
    if "entries" in doc and "final_deg" in doc:
        doc["_kind"] = "suite"
    elif "bones" in doc and "body_score_deg" in doc:
        doc["_kind"] = "score"
    else:
        raise ValueError(f"{path}: neither a suite report nor a score file")
    return doc


def suite_label(doc: dict) -> str:
    how = [doc.get("readout", "json"), doc.get("calibration", "rules")]
    extra = "" if how == ["json", "rules"] else f" [{how[0]}, {how[1]}]"
    return f"{doc['implementation']}{extra}"


def score_label(doc: dict) -> str:
    stem = Path(doc["_path"]).name
    for suffix in (".score.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def _delta(value: float, base: Optional[float]) -> str:
    return "" if base is None else f" ({value - base:+.2f})"


def suite_table(docs: Sequence[dict], baseline: Optional[int] = 0) -> dict:
    """Rows = suite runs, columns = final, quality, then every dataset/tracker-set entry."""
    columns: List[str] = []
    for d in docs:
        for e in d["entries"]:
            key = f"{e['dataset']} {e['tracker_set']}"
            if key not in columns:
                columns.append(key)
    rows = []
    for d in docs:
        entries = {f"{e['dataset']} {e['tracker_set']}": e["weighted_score_deg"] for e in d["entries"]}
        rows.append({"label": suite_label(d), "suite": d.get("suite", ""), "final_deg": d["final_deg"], "quality": d["quality"],
                     "entries": entries, "path": d["_path"]})
    base = rows[baseline] if baseline is not None and 0 <= baseline < len(rows) else None
    for r in rows:
        r["delta_final_deg"] = None if base is None else r["final_deg"] - base["final_deg"]
    return {"kind": "suite", "columns": columns, "rows": rows, "baseline": base["label"] if base else None}


def score_table(docs: Sequence[dict], baseline: Optional[int] = 0) -> dict:
    """Rows = bones (plus summary rows), columns = runs; deltas against the baseline run."""
    bones: List[str] = []
    for d in docs:
        for b in d["bones"]:
            if b not in bones:
                bones.append(b)
    runs = [{"label": score_label(d), "implementation": d.get("implementation", ""), "tracker_set": d.get("tracker_set", ""),
             "body_score_deg": d["body_score_deg"], "weighted_score_deg": d["weighted_score_deg"],
             "end_effector_cm": d["end_effector_position_mean_m"] * 100.0, "frames": d.get("frames_scored", 0),
             "bones": {b: s["angle_mean_deg"] for b, s in d["bones"].items()}, "path": d["_path"]} for d in docs]
    base = runs[baseline] if baseline is not None and 0 <= baseline < len(runs) else None
    worst = []
    if base is not None:
        for r in runs:
            if r is base:
                continue
            deltas = sorted(((r["bones"][b] - base["bones"][b], b) for b in bones if b in r["bones"] and b in base["bones"]),
                            reverse=True)
            worst.append({"run": r["label"], "worse": [(b, v) for v, b in deltas[:5] if v > 0],
                          "better": [(b, v) for v, b in deltas[::-1][:5] if v < 0]})
    return {"kind": "score", "bones": bones, "runs": runs, "baseline": base["label"] if base else None, "largest_changes": worst}


def markdown(table: dict) -> str:
    out: List[str] = []
    if table["kind"] == "suite":
        out.append("| run | final° | quality | " + " | ".join(table["columns"]) + " |")
        out.append("|---|---|---|" + "---|" * len(table["columns"]))
        base = next((r for r in table["rows"] if r["label"] == table["baseline"]), None)
        for r in table["rows"]:
            d = "" if base is None or r is base else f" ({r['delta_final_deg']:+.2f})"
            cells = []
            for c in table["columns"]:
                v = r["entries"].get(c)
                b = None if base is None or r is base else base["entries"].get(c)
                cells.append("" if v is None else f"{v:.2f}{_delta(v, b)}")
            out.append(f"| {r['label']} | **{r['final_deg']:.2f}**{d} | {r['quality']:.1f} | " + " | ".join(cells) + " |")
        if base is not None and len(table["rows"]) > 1:
            out.append(f"\nDeltas in parentheses are against `{base['label']}`.")
        return "\n".join(out)
    runs = table["runs"]
    base = next((r for r in runs if r["label"] == table["baseline"]), None)
    out.append("| bone | " + " | ".join(r["label"] for r in runs) + " |")
    out.append("|---|" + "---|" * len(runs))
    for key, title, fmt in (("body_score_deg", "**body score°**", "{:.2f}"), ("weighted_score_deg", "**weighted°**", "{:.2f}"),
                            ("end_effector_cm", "**end effectors cm**", "{:.1f}"), ("frames", "frames", "{:d}")):
        cells = []
        for r in runs:
            cell = fmt.format(r[key])
            if base is not None and r is not base and key != "frames":
                cell += f" ({r[key] - base[key]:+.2f})"
            cells.append(cell)
        out.append(f"| {title} | " + " | ".join(cells) + " |")
    for b in table["bones"]:
        cells = []
        for r in runs:
            v = r["bones"].get(b)
            bv = None if base is None or r is base else base["bones"].get(b)
            cells.append("" if v is None else f"{v:.2f}{_delta(v, bv)}")
        out.append(f"| {b} | " + " | ".join(cells) + " |")
    for w in table["largest_changes"]:
        worse = ", ".join(f"{b} {v:+.1f}°" for b, v in w["worse"]) or "none"
        better = ", ".join(f"{b} {v:+.1f}°" for b, v in w["better"]) or "none"
        out.append(f"\n`{w['run']}` against `{table['baseline']}`: worse on {worse}; better on {better}.")
    return "\n".join(out)


def build(paths: Sequence, baseline: Optional[int] = 0) -> List[dict]:
    docs = [load(p) for p in paths]
    tables = []
    suites = [d for d in docs if d["_kind"] == "suite"]
    scores = [d for d in docs if d["_kind"] == "score"]
    if suites:
        tables.append(suite_table(suites, baseline))
    if scores:
        tables.append(score_table(scores, baseline))
    return tables


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ikh report", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="*", help="suite reports and/or score files (default: out/suite/*.json)")
    p.add_argument("--baseline", type=int, default=0, help="index of the run the deltas refer to; -1 for no deltas")
    p.add_argument("--out", default=None, help="write the markdown here as well")
    p.add_argument("--json", default=None, help="write the tables as JSON")
    args = p.parse_args(argv)
    files = args.files or sorted((ROOT / "out" / "suite").glob("*.json"))
    if not files:
        print("ikh report: nothing to report (run ikh suite first, or name files)", file=sys.stderr)
        return 2
    tables = build(files, None if args.baseline < 0 else args.baseline)
    text = "\n\n".join(markdown(t) for t in tables)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    if args.json:
        Path(args.json).write_text(json.dumps(tables, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
