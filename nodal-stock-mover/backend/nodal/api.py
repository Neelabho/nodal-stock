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


@app.get("/api/map/products")
def map_products():
    r = _result()
    seen = {}
    for p in r["positions"]:
        seen.setdefault(p["style"], {"style": p["style"], "name": p["style_name"], "category": p["category"], "sizes": set()})["sizes"].add(p["size"])
    return {"categories": sorted({v["category"] for v in seen.values()}),
            "styles": [{**v, "sizes": sorted(v["sizes"])} for v in sorted(seen.values(), key=lambda v: (v["category"], v["style"]))]}


@app.get("/api/map")
def map_view(style: str | None = None, category: str | None = None, size: str | None = None):
    """Per-store demand / stock / gap for one product (style, optionally one size) or a whole category, plus the moves planned for it."""
    from . import geo
    r = _result()
    if not style and not category:
        raise HTTPException(422, "pass style= or category=")
    sel = [p for p in r["positions"] if (style is None or p["style"] == style) and (category is None or p["category"] == category) and (size is None or p["size"] == size)]
    stores = {s["store_id"]: s for s in r["stores"]}
    import pandas as pd
    coords = geo.place(pd.DataFrame(r["stores"]).assign(city=lambda x: x.city))
    agg = {}
    for p in sel:
        a = agg.setdefault(p["store"], {"demand_28d": 0.0, "on_hand": 0, "in_transit": 0, "plan_in": 0, "plan_out": 0, "units84": 0})
        a["demand_28d"] += p["daily"] * 28; a["on_hand"] += p["on_hand"]; a["in_transit"] += p["in_transit"]; a["plan_in"] += p["plan_in"]; a["plan_out"] += p["plan_out"]; a["units84"] += p["units84"]
    out = []
    for sid, a in agg.items():
        if sid not in coords:
            continue
        stock = a["on_hand"] + a["in_transit"]
        after = stock + a["plan_in"] - a["plan_out"]
        daily = a["demand_28d"] / 28
        out.append({"store": sid, "city": stores[sid]["city"], "tier": stores[sid]["tier"], "lat": coords[sid][0], "lon": coords[sid][1], "demand_28d": round(a["demand_28d"], 1), "units_84d": a["units84"],
                    "stock": stock, "plan_in": a["plan_in"], "plan_out": a["plan_out"], "stock_after_plan": after, "cover_days": None if daily < 1e-6 else round(stock / daily, 1),
                    "cover_after_days": None if daily < 1e-6 else round(after / daily, 1), "gap_units": round(max(0.0, a["demand_28d"] - after), 1), "surplus_units": round(max(0.0, after - a["demand_28d"] * 3), 1)})
    ids = {p["sku"] for p in sel}
    moves = []
    for a in r["actions"]:
        if a["sku_id"] in ids and (a["source"] in coords or a["source"] == "WH-KOL") and (a["destination"] in coords or a["destination"] == "WH-KOL"):
            moves.append({"id": a["id"], "type": a["type"], "from": a["source"], "to": a["destination"], "qty": a["qty"], "sku": a["sku_id"]})
    wh = [w for w in r["warehouse"] if w["sku"] in ids]
    la, lo = geo.CITY_LATLON[geo.WAREHOUSE_CITY]
    return {"stores": out, "moves": moves, "warehouse": {"lat": la, "lon": lo, "on_hand": sum(w["on_hand"] for w in wh), "plan_out": sum(w["plan_out"] for w in wh)},
            "note": "Store positions are approximate city-centre placements (the data has cities, not coordinates)."}


@app.get("/api/backtest")
def backtest():
    p = Path(__file__).resolve().parents[2] / "docs" / "FORECAST_BACKTEST.json"
    if not p.exists():
        raise HTTPException(404, "back-test not generated yet: run  python -m nodal.cli --backtest")
    import json
    return json.loads(p.read_text())


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
