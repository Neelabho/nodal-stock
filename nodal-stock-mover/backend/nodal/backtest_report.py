"""Run the forecast back-test and persist it (takes ~40 s, so it is a CLI step, not an API call)."""
from __future__ import annotations
import datetime as dt, json
from pathlib import Path
from .loader import load_all
from .ml_eval import run_suite


def write(data_dir, out: Path):
    d = load_all(data_dir)
    rep = run_suite(d)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "source_hash": d.source_hash,
                               "method": "rolling origin; model trained only on windows that ended before each test origin; metrics on pairs that sold in the prior 52 weeks; config tuned on earlier validation origins only", **rep}, indent=1, default=float))
    print(f"wrote {out}")
