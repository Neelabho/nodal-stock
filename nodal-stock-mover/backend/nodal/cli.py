"""Headless run (no UI): python -m nodal.cli [--data DIR] [--out DIR]  ->  actions.csv + run_manifest.json"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
from .engine import run

def main():
    from .envfile import load_env
    load_env()                       # SARVAM_API_KEY etc. from the git-ignored .env (real environment variables win)
    root = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(root / "manyavar-vch3-data" / "csv"))
    ap.add_argument("--out", default=str(root / "nodal-stock-mover" / "data"))
    ap.add_argument("command", nargs="?", choices=["daily", "schedule", "watch", "export"], help="daily = full pipeline now; schedule = run every day at NODAL_DAILY_TIME; watch = process new datasets dropped in the inbox")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--backtest", action="store_true", help="re-run the rolling-origin forecast back-test and write docs/FORECAST_BACKTEST.json")
    a = ap.parse_args()
    if a.command:
        import logging, json as _j
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        from . import pipeline
        if a.command == "daily":
            out = pipeline.daily(force=a.force, with_ai=not a.no_ai)
            print(_j.dumps({k: v for k, v in out.items() if k != "bundle"}, indent=1, default=str)); return
        if a.command == "schedule":
            return pipeline.schedule_forever()
        if a.command == "watch":
            return pipeline.watch_inbox()
    if a.backtest:
        from .backtest_report import write
        write(a.data, root / "nodal-stock-mover" / "docs" / "FORECAST_BACKTEST.json")
        return
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
