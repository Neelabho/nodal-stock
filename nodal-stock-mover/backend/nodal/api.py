"""FastAPI app: JSON API + static UI. The plan is computed on demand from the CSVs and cached in memory."""
from __future__ import annotations
import csv, io, os, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import run
from .loader import DataError

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("NODAL_DATA_DIR", ROOT / "manyavar-vch3-data" / "csv"))
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="NODAL — Stock Mover (Brief B)", version="0.1.0")
_lock = threading.Lock()
_state: dict = {}


def _result(refresh: bool = False) -> dict:
    with _lock:
        if refresh or "r" not in _state:
            try:
                _state["r"] = run(str(DATA_DIR))
                _state["by_id"] = {a["id"]: a for a in _state["r"]["actions"]}
                _state["error"] = None
            except DataError as e:
                _state["error"] = str(e)
                raise HTTPException(422, detail=str(e))
        return _state["r"]


LIGHT = ["id", "type", "type_label", "reason", "reason_label", "urgency", "priority_tier", "sku_id", "style_id", "style_name", "category", "size", "source", "destination", "qty",
         "dispatch_date", "arrival_date", "lead_days", "unit_mrp", "value_mrp", "cover_before_days", "cover_after_days", "confidence", "flags", "supply_basis", "requires_approval", "status", "explanation"]


@app.get("/api/overview")
def overview(refresh: bool = False):
    r = _result(refresh)
    return {"manifest": r["manifest"], "summary": r["summary"], "validation": r["validation"], "stores": r["stores"]}


@app.post("/api/run")
def rerun():
    r = _result(True)
    return {"ok": True, "actions": len(r["actions"]), "runtime_seconds": r["manifest"]["runtime_seconds"]}


@app.get("/api/actions")
def actions():
    r = _result()
    return [{k: a[k] for k in LIGHT} for a in r["actions"]]


@app.get("/api/actions/{aid}")
def action(aid: str):
    _result()
    a = _state["by_id"].get(aid)
    if not a:
        raise HTTPException(404, "unknown action id")
    return a


@app.get("/api/audit")
def audit():
    return _result()["audit"]


@app.get("/api/safety")
def safety():
    r = _result()
    return {"blocked_samples": r["blocked_samples"], "blocked_counts": r["summary"]["blocked_counts"], "unresolved": r["unresolved"], "excluded": r["excluded"], "validation": r["validation"]}


@app.get("/api/manifest")
def manifest():
    return _result()["manifest"]


@app.get("/api/export.csv")
def export_csv():
    r = _result()
    cols = ["id", "urgency", "type_label", "reason_label", "sku_id", "style_name", "category", "size", "source", "destination", "qty", "dispatch_date", "arrival_date", "lead_days",
            "unit_mrp", "value_mrp", "cover_before_days", "cover_after_days", "confidence", "requires_approval", "supply_basis", "flags", "explanation"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for a in r["actions"]:
        w.writerow([";".join(a[c]) if c == "flags" else a[c] for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=nodal_actions.csv"})


@app.exception_handler(DataError)
def data_error(_, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
