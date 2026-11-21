"""Headless run (no UI): python -m nodal.cli [--data DIR] [--out DIR]  ->  actions.csv + run_manifest.json"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
from .engine import run

def main():
    root = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(root / "manyavar-vch3-data" / "csv"))
    ap.add_argument("--out", default=str(root / "nodal-stock-mover" / "data"))
    a = ap.parse_args()
    r = run(a.data)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cols = ["id", "urgency", "type_label", "reason_label", "sku_id", "size", "source", "destination", "qty", "dispatch_date", "arrival_date", "value_mrp", "requires_approval", "explanation"]
    with open(out / "actions.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols)
        for x in r["actions"]: w.writerow([x[c] for c in cols])
    (out / "run_manifest.json").write_text(json.dumps({"manifest": r["manifest"], "summary": r["summary"], "validation": r["validation"]}, indent=1, default=str))
    s = r["summary"]
    print(f"{s['total']['actions']} actions, {s['total']['units']} units; validator violations: {sum(x['violations'] for x in r['validation']['rules'])}; wrote {out}")

if __name__ == "__main__":
    main()
