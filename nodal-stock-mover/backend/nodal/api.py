"""FastAPI app: JSON API + static PWA.

Modes (NODAL_MODE): "auto" (default) serves the latest published run bundle when one exists, otherwise computes the plan from the CSVs;
"published" never computes (light public deployment: no ML libraries needed); "compute" always recomputes.
Decisions are stored server-side (append-only audit log). Approving a plan is NOT executing a stock transfer.
"""
from __future__ import annotations
import csv, io, json, logging, os, re, threading
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, pipeline, geo, shipments as shp
from .envfile import load_env

load_env()

log = logging.getLogger("nodal.api")
ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("NODAL_DATA_DIR", ROOT / "manyavar-vch3-data" / "csv"))
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
MODE = os.environ.get("NODAL_MODE", "auto")
BUNDLE = os.environ.get("NODAL_BUNDLE")

app = FastAPI(title="NODAL — Stock Mover (Brief B)", version="0.2.0")
_lock = threading.Lock()
_state: dict = {}


def _load(refresh=False) -> dict:
    with _lock:
        if refresh or "r" not in _state:
            b = None if MODE == "compute" else pipeline.load_bundle(Path(os.environ.get("NODAL_BUNDLE")) if os.environ.get("NODAL_BUNDLE") else None)
            if b is not None:
                pipeline.import_bundle_to_db(b)
                _state.update(r=b, run_id=b["run_id"], created_at=b["created_at"], source="published")
            elif MODE == "published":
                raise HTTPException(503, detail="No published run found. Run `python -m nodal.cli daily` and commit published/latest.json.gz.")
            else:
                from .engine import run                      # heavy imports only when computing
                from .required import DataError
                try:
                    r = run(str(DATA_DIR))
                except DataError as e:
                    raise HTTPException(422, detail=str(e))
                run_id = "manual-" + db.now().replace(":", "").replace("-", "")[:15]
                db.create_run(run_id, "manual", r["manifest"]["as_of"], r["manifest"]["source_hash"], status="completed")
                db.store_moves(run_id, r["actions"])
                r["reviews"] = {}
                _state.update(r=r, run_id=run_id, created_at=db.now(), source="computed")
            if not _state["r"].get("shipments"):                       # older bundles / computed plans: build with template summaries
                rv = {k: {"outcome": v.get("outcome")} for k, v in (_state["r"].get("reviews") or {}).items() if v}
                sh = shp.build(_state["r"]["actions"], _state["r"]["stores"], _state["r"]["manifest"]["seasonality"], rv)
                shp.summarise(sh, None)
                _state["r"]["shipments"] = sh
            _state["by_id"] = {a["id"]: a for a in _state["r"]["actions"]}
            _state["ship_by_id"] = {s["id"]: s for s in _state["r"]["shipments"]}
        return _state["r"]


def _result(refresh=False):
    return _load(refresh)


def _reviews() -> dict:
    return _state["r"].get("reviews") or {}


LIGHT = ["id", "type", "type_label", "reason", "reason_label", "urgency", "priority_tier", "sku_id", "style_id", "style_name", "category", "size", "source", "destination", "qty",
         "dispatch_date", "arrival_date", "lead_days", "unit_mrp", "value_mrp", "cover_before_days", "cover_after_days", "confidence", "flags", "supply_basis", "requires_approval", "status", "explanation", "explanation_lines"]


def _ai_brief(mid: str) -> dict:
    rv = _reviews().get(mid)
    if not rv:
        return {"ai_state": "none", "ai_outcome": None, "has_alt": False}
    return {"ai_state": rv["state"], "ai_outcome": rv.get("outcome"), "has_alt": bool(rv.get("alternative"))}


def _actor(x_client_id: str | None) -> str:
    return "judge-" + re.sub(r"[^A-Za-z0-9_-]", "", x_client_id or "")[:24] if x_client_id else "anonymous"


@app.get("/healthz")
def healthz():
    out = {"ok": True, "mode": MODE, "db": db.healthy()}
    try:
        _load()
        out.update(run_id=_state["run_id"], source=_state["source"])
    except HTTPException as e:
        out.update(ok=False, error=e.detail)
    out["ok"] = out["ok"] and out["db"]["ok"]
    return JSONResponse(out, status_code=200 if out["ok"] else 503)


@app.get("/api/overview")
def overview(refresh: bool = False):
    r = _result(refresh)
    return {"manifest": r["manifest"], "summary": r["summary"], "validation": r["validation"], "stores": r["stores"], "run_id": _state["run_id"], "created_at": _state["created_at"], "source": _state["source"]}


@app.get("/api/status")
def status():
    """Landing-page numbers for the phone dashboard."""
    r = _result()
    s = r["summary"]
    dec = db.current_decisions(_state["run_id"])
    runs = db.list_runs(1)
    rv = _reviews()
    counts = {}
    for v in rv.values():
        counts[v["state"]] = counts.get(v["state"], 0) + 1
    run = db.get_run(_state["run_id"]) or {}
    return {"run_id": _state["run_id"], "created_at": _state["created_at"], "as_of": r["manifest"]["as_of"], "source": _state["source"],
            "moves": s["total"]["actions"], "units": s["total"]["units"], "value_mrp": s["total"]["value_mrp"], "needs_signoff": s["needs_approval"],
            "awaiting": s["total"]["actions"] - sum(1 for d in dec.values() if d["decision"] in ("approved", "approved_alternative", "rejected")),
            "approved": sum(1 for d in dec.values() if d["decision"] in ("approved", "approved_alternative")), "rejected": sum(1 for d in dec.values() if d["decision"] == "rejected"),
            "ai": {"reviews_completed": counts.get("completed", 0), "failed": counts.get("failed", 0), "pending": counts.get("pending", 0), "skipped": counts.get("skipped", 0),
                   "alternatives": sum(1 for v in rv.values() if v.get("alternative")), "flagged": sum(1 for v in rv.values() if v.get("outcome") == "FLAG"), "model": next((v.get("model") for v in rv.values() if v.get("model")), None)},
            "dq_warnings": sum(1 for f in r["audit"]["findings"] if f["severity"] in ("warn", "error")),
            "rules_passed": sum(1 for x in r["validation"]["rules"] if x["passed"]), "rules_total": len(r["validation"]["rules"]),
            "automation": {"last_run_status": (runs[0]["status"] if runs else run.get("status")), "last_run_at": runs[0]["created_at"] if runs else None, "ai_status": run.get("ai_status"),
                           "schedule": os.environ.get("NODAL_DAILY_TIME", "06:00") + " " + os.environ.get("NODAL_TZ", "Asia/Kolkata")}}


@app.post("/api/run")
def rerun():
    if MODE == "published":
        raise HTTPException(501, "this deployment serves published runs only")
    r = _result(True)
    return {"ok": True, "actions": len(r["actions"]), "runtime_seconds": r["manifest"]["runtime_seconds"]}


@app.get("/api/actions")
def actions():
    r = _result()
    dec = db.current_decisions(_state["run_id"])
    return [{**{k: a.get(k) for k in LIGHT}, **_ai_brief(a["id"]), "decision": (dec.get(a["id"]) or {}).get("decision")} for a in r["actions"]]


@app.get("/api/actions/{aid}")
def action(aid: str):
    _result()
    a = _state["by_id"].get(aid)
    if not a:
        raise HTTPException(404, "unknown action id")
    rv = _reviews().get(aid)
    return {**a, "review": rv, "decision": db.current_decisions(_state["run_id"]).get(aid)}


class DecisionIn(BaseModel):
    move_id: str
    decision: Literal["approved", "approved_alternative", "rejected", "cleared"]
    reason: str | None = None
    confirm_high_value: bool = False


@app.post("/api/decisions")
def decide(body: DecisionIn, x_client_id: str | None = Header(default=None)):
    _result()
    a = _state["by_id"].get(body.move_id)
    if not a:
        raise HTTPException(404, "unknown move")
    rv = _reviews().get(body.move_id) or {}
    alt = rv.get("alternative")
    target = alt["action"] if (body.decision == "approved_alternative" and alt) else a
    if body.decision == "approved_alternative" and not alt:
        raise HTTPException(422, "no validated AI alternative exists for this move")
    if body.decision in ("approved", "approved_alternative") and target["requires_approval"] and not body.confirm_high_value:
        raise HTTPException(409, {"error": "needs_confirmation", "message": f"This move is worth ₹{target['value_mrp']:,} at MRP (above the sign-off limit). Confirm explicitly."})
    actor = _actor(x_client_id)
    out = db.record_decision(_state["run_id"], body.move_id, body.decision, actor, body.reason, original={k: a.get(k) for k in LIGHT}, ai_alternative=alt,
                             confirmed=body.confirm_high_value)
    if out["written"]:
        db.audit("decision", actor, run=_state["run_id"], move=body.move_id, decision=body.decision)
    return {**out, "note": "This approves the proposed plan only. It does not dispatch any stock."}


@app.get("/api/decisions")
def decisions():
    _result()
    return {"run_id": _state["run_id"], "current": db.current_decisions(_state["run_id"])}


# ---------------------------------------------------------------- shipments + batch decisions
def _ship_status(sh: dict, dec: dict) -> dict:
    ds = [(dec.get(m) or {}).get("decision") for m in sh["move_ids"]]
    ok = sum(1 for d in ds if d in ("approved", "approved_alternative")); no = sum(1 for d in ds if d == "rejected"); n = len(ds)
    status = "approved" if ok == n else "rejected" if no == n else "pending" if ok + no == 0 else "mixed"
    return {"status": status, "approved": ok, "rejected": no}


@app.get("/api/shipments")
def shipments_list():
    _result()
    dec = db.current_decisions(_state["run_id"])
    return [{**{k: v for k, v in s.items() if k != "move_ids"}, **_ship_status(s, dec), "n_moves": len(s["move_ids"])} for s in _state["r"]["shipments"]]


@app.get("/api/shipments/{sid}")
def shipment_detail(sid: str):
    _result()
    s = _state["ship_by_id"].get(sid)
    if not s:
        raise HTTPException(404, "unknown shipment")
    dec = db.current_decisions(_state["run_id"])
    lines = [{**{k: a[k] for k in ("id", "type", "type_label", "reason_label", "urgency", "sku_id", "style_name", "size", "source", "destination", "qty", "arrival_date", "value_mrp", "requires_approval", "explanation")},
              **_ai_brief(a["id"]), "decision": (dec.get(a["id"]) or {}).get("decision")} for a in (_state["by_id"][m] for m in s["move_ids"])]
    return {**{k: v for k, v in s.items() if k != "move_ids"}, **_ship_status(s, dec), "moves": lines}


class BatchIn(BaseModel):
    shipment_id: str | None = None
    move_ids: list[str] | None = None
    decision: Literal["approved", "rejected", "cleared"]
    confirm_high_value: bool = False
    reason: str | None = None


def _apply_batch(ids: list[str], decision: str, actor: str, confirm: bool, reason: str | None, label: str):
    moves = [_state["by_id"][m] for m in ids if m in _state["by_id"]]
    big = [a for a in moves if a["requires_approval"]]
    if decision == "approved" and big and not confirm:
        raise HTTPException(409, {"error": "needs_confirmation", "big_items": len(big), "big_value": sum(a["value_mrp"] for a in big),
                                  "message": f"{len(big)} item(s) in this selection are above the ₹{shp_threshold():,} sign-off limit (₹{sum(a['value_mrp'] for a in big):,} together). Confirm to approve everything."})
    written = 0
    for a in moves:
        out = db.record_decision(_state["run_id"], a["id"], decision, actor, reason, original={k: a.get(k) for k in LIGHT}, confirmed=confirm and a["requires_approval"])
        written += int(out["written"])
    db.audit("batch_decision", actor, run=_state["run_id"], scope=label, decision=decision, moves=len(moves), written=written)
    return {"moves": len(moves), "written": written, "note": "This approves the proposed plan only. It does not dispatch any stock."}


def shp_threshold() -> int:
    return int(_state["r"]["manifest"]["policy"]["approval_value_mrp"])


@app.post("/api/decisions/batch")
def decide_batch(body: BatchIn, x_client_id: str | None = Header(default=None)):
    _result()
    if body.shipment_id:
        s = _state["ship_by_id"].get(body.shipment_id)
        if not s:
            raise HTTPException(404, "unknown shipment")
        ids = s["move_ids"]
    elif body.move_ids:
        ids = body.move_ids
    else:
        raise HTTPException(422, "pass shipment_id or move_ids")
    return _apply_batch(ids, body.decision, _actor(x_client_id), body.confirm_high_value, body.reason, body.shipment_id or "moves")


class RoutineIn(BaseModel):
    confirm_high_value: bool = False


@app.post("/api/decisions/routine")
def decide_routine(body: RoutineIn, x_client_id: str | None = Header(default=None)):
    """Approve every pending shipment marked routine (no AI flags, no low-confidence items, not critical)."""
    _result()
    dec = db.current_decisions(_state["run_id"])
    ids = [m for s in _state["r"]["shipments"] if s["routine"] and _ship_status(s, dec)["status"] == "pending" for m in s["move_ids"]]
    if not ids:
        return {"moves": 0, "written": 0, "note": "No pending routine shipments."}
    return _apply_batch(ids, "approved", _actor(x_client_id), body.confirm_high_value, "approved as routine", "routine")


# ---------------------------------------------------------------- Add data (local only): upload CSVs, recompute, progress
_job = {"running": False, "started": None, "result": None}
_job_lock = threading.Lock()


def _local_only(request: Request, token: str | None = None):
    if MODE == "published":
        raise HTTPException(403, "Uploads and recalculation are disabled on the public site. Run NODAL on the operator machine to add data.")
    host = request.client.host if request.client else ""
    want = os.environ.get("NODAL_ADMIN_TOKEN")
    if host not in ("127.0.0.1", "::1", "localhost") and not (want and token == want):
        raise HTTPException(403, "Uploads are only accepted from the machine running NODAL (or with the admin token).")


@app.get("/api/inbox")
def inbox(request: Request):
    _local_only(request)
    return {"files": pipeline.inbox_status(), "dataset": pipeline.resolve_dataset()[1], "job": _job}


@app.put("/api/inbox/{name}")
async def inbox_put(name: str, request: Request, x_admin_token: str | None = Header(default=None)):
    _local_only(request, x_admin_token)
    base = name[:-4] if name.endswith(".csv") else name
    body = await request.body()
    if len(body) > 80_000_000:
        raise HTTPException(413, "file too large")
    problems = pipeline.validate_upload(base, body)
    if problems:
        raise HTTPException(422, {"file": base, "problems": problems})
    d = pipeline.inbox_dir(); d.mkdir(parents=True, exist_ok=True)
    (d / f"{base}.csv").write_bytes(body)
    db.audit("dataset_uploaded", "operator", name=base, bytes=len(body))
    return {"saved": f"{base}.csv", "bytes": len(body)}


@app.delete("/api/inbox/{name}")
def inbox_delete(name: str, request: Request):
    _local_only(request)
    p = pipeline.inbox_dir() / f"{name}.csv"
    if p.exists():
        p.unlink()
        db.audit("dataset_removed", "operator", name=name)
    return {"removed": p.name}


def _run_job(force: bool, with_ai: bool):
    try:
        out = pipeline.daily(force=force, with_ai=with_ai)
        if out.get("status") in ("completed", "partial"):
            _load(True)
        _job["result"] = {k: v for k, v in out.items() if k != "bundle"}
    except Exception as e:  # noqa: BLE001
        _job["result"] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        _job["running"] = False


@app.post("/api/pipeline/run")
def pipeline_run(request: Request, ai: bool = True, force: bool = False):
    _local_only(request)
    with _job_lock:
        if _job["running"]:
            raise HTTPException(409, "A calculation is already running.")
        _job.update(running=True, started=db.now(), result=None)
    threading.Thread(target=_run_job, args=(force, ai), daemon=True).start()
    return {"started": True}


@app.get("/api/pipeline/status")
def pipeline_status(request: Request):
    _local_only(request)
    last = (db.list_runs(1) or [None])[0]
    return {"running": _job["running"], "started": _job["started"], "result": _job["result"],
            "run": None if not last else {k: last[k] for k in ("run_id", "status", "stages", "ai_status", "metrics", "error", "created_at")}}


class AskIn(BaseModel):
    question: str


@app.post("/api/ask")
def ask(body: AskIn, request: Request, x_client_id: str | None = Header(default=None)):
    from . import qa
    _result()
    cid = _actor(x_client_id) if x_client_id else "ip-" + (request.client.host if request.client else "x")
    dec = db.current_decisions(_state["run_id"])
    ships = [{**s, **_ship_status(s, dec)} for s in _state["r"]["shipments"]]
    s = _state["r"]["summary"]
    status = {"shipments": len(ships), "units": s["total"]["units"], "needs_signoff": s["needs_approval"], "approved": sum(1 for d in dec.values() if d["decision"] != "rejected"),
              "awaiting": s["total"]["actions"] - len(dec)}
    local = None
    if MODE != "published":
        from .ai.ollama import OllamaClient
        local = OllamaClient()
    try:
        return qa.answer(body.question, cid, _state["run_id"], _state["r"]["manifest"]["policy"], ships, status, local_client=local)
    except qa.RateLimited as e:
        raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after)})
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.get("/api/ask/suggestions")
def ask_suggestions():
    from . import qa
    return qa.SUGGESTIONS


@app.get("/api/capabilities")
def capabilities():
    local = MODE != "published"
    return {"mode": MODE, "can_upload": local, "can_compute": local, "can_ask": True, "general_questions": bool(os.environ.get("SARVAM_API_KEY")), "public": not local}


@app.get("/api/ai")
def ai_overview():
    r = _result()
    rv = _reviews()
    by = {a["id"]: a for a in r["actions"]}
    rows = []
    for mid, v in rv.items():
        if v["state"] in ("completed", "failed", "pending"):
            a = by.get(mid)
            if a:
                rows.append({"id": mid, "state": v["state"], "outcome": v.get("outcome"), "error": v.get("error"), "review": v.get("review"), "alternative": v.get("alternative"),
                             "move": {k: a[k] for k in ("type_label", "reason_label", "sku_id", "style_name", "qty", "source", "destination", "value_mrp", "requires_approval", "explanation", "arrival_date", "urgency")}})
    order = {"ALTERNATIVE": 0, "FLAG": 1, "INSUFFICIENT_EVIDENCE": 2, "CONFIRM": 3, None: 4}
    rows.sort(key=lambda x: (order.get(x["outcome"], 4), -x["move"]["value_mrp"]))
    states = {}
    for v in rv.values():
        states[v["state"]] = states.get(v["state"], 0) + 1
    outcomes = {}
    for x in rows:
        outcomes[x["outcome"] or x["state"]] = outcomes.get(x["outcome"] or x["state"], 0) + 1
    cands = sum(len((x["review"] or {}).get("candidates_considered", [])) for x in rows)
    better = sum(1 for x in rows for c in (x["review"] or {}).get("candidates_considered", []) if c["delta_expected_revenue"] > 0)
    return {"states": states, "outcomes": outcomes, "candidates_evaluated": cands, "better_by_calculation": better, "reviews": rows,
            "note": "AI conclusions are advisory and kept separate from the deterministic validator. Model-stated confidence is not a calibrated probability."}


@app.get("/api/runs")
def runs():
    _result()
    return [{k: v for k, v in r.items() if k != "error" or v} for r in db.list_runs(40)]


@app.get("/api/history")
def history():
    _result()
    log_ = db.decision_log(None, 300)
    by = {}
    for rid in {x["run_id"] for x in log_}:
        by[rid] = {m["id"]: m for m in db.moves_of(rid)}
    out = []
    for x in log_:
        m = by.get(x["run_id"], {}).get(x["move_id"], {})
        out.append({"ts": x["ts"], "run_id": x["run_id"], "move_id": x["move_id"], "decision": x["decision"], "actor": x["actor"], "reason": x["reason"], "confirmed": x["confirmed_high_value"],
                    "had_ai_alternative": bool(x["ai_alternative"]), "move": {k: m.get(k) for k in ("type_label", "sku_id", "style_name", "qty", "source", "destination", "value_mrp")}})
    return {"decisions": out, "audit": [{"ts": e["ts"], "event": e["event"], "actor": e["actor"]} for e in _audit_tail()],
            "outcomes_recorded": _count_outcomes(), "note": "Outcomes (actual moves, later sales) are recorded here once supplied; no model or rule is retrained from single decisions."}


def _audit_tail(n=40):
    import sqlalchemy as sa
    with db.engine().connect() as c:
        return [dict(r) for r in c.execute(sa.select(db.audit_events).order_by(db.audit_events.c.id.desc()).limit(n)).mappings()]


def _count_outcomes():
    import sqlalchemy as sa
    with db.engine().connect() as c:
        return c.execute(sa.select(sa.func.count()).select_from(db.outcomes)).scalar() or 0


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


@app.get("/api/map/products")
def map_products():
    r = _result()
    seen = {}
    for p in r["positions"]:
        seen.setdefault(p["style"], {"style": p["style"], "name": p["style_name"], "category": p["category"], "sizes": set()})["sizes"].add(p["size"])
    return {"categories": sorted({v["category"] for v in seen.values()}), "styles": [{**v, "sizes": sorted(v["sizes"])} for v in sorted(seen.values(), key=lambda v: (v["category"], v["style"]))]}


@app.get("/api/map")
def map_view(style: str | None = None, category: str | None = None, size: str | None = None):
    r = _result()
    if not style and not category:
        raise HTTPException(422, "pass style= or category=")
    sel = [p for p in r["positions"] if (style is None or p["style"] == style) and (category is None or p["category"] == category) and (size is None or p["size"] == size)]
    stores = {s["store_id"]: s for s in r["stores"]}
    coords = geo.place(r["stores"])
    agg = {}
    for p in sel:
        a = agg.setdefault(p["store"], {"demand_28d": 0.0, "on_hand": 0, "in_transit": 0, "plan_in": 0, "plan_out": 0, "units84": 0})
        a["demand_28d"] += p["daily"] * 28; a["on_hand"] += p["on_hand"]; a["in_transit"] += p["in_transit"]; a["plan_in"] += p["plan_in"]; a["plan_out"] += p["plan_out"]; a["units84"] += p["units84"]
    out = []
    for sid, a in agg.items():
        if sid not in coords:
            continue
        stock = a["on_hand"] + a["in_transit"]; after = stock + a["plan_in"] - a["plan_out"]; daily = a["demand_28d"] / 28
        out.append({"store": sid, "city": stores[sid]["city"], "tier": stores[sid]["tier"], "lat": coords[sid][0], "lon": coords[sid][1], "demand_28d": round(a["demand_28d"], 1), "units_84d": a["units84"],
                    "stock": stock, "plan_in": a["plan_in"], "plan_out": a["plan_out"], "stock_after_plan": after, "cover_days": None if daily < 1e-6 else round(stock / daily, 1),
                    "cover_after_days": None if daily < 1e-6 else round(after / daily, 1), "gap_units": round(max(0.0, a["demand_28d"] - after), 1), "surplus_units": round(max(0.0, after - a["demand_28d"] * 3), 1)})
    ids = {p["sku"] for p in sel}
    moves = [{"id": a["id"], "type": a["type"], "from": a["source"], "to": a["destination"], "qty": a["qty"], "sku": a["sku_id"]} for a in r["actions"]
             if a["sku_id"] in ids and (a["source"] in coords or a["source"] == "WH-KOL") and (a["destination"] in coords or a["destination"] == "WH-KOL")]
    wh = [w for w in r["warehouse"] if w["sku"] in ids]
    la, lo = geo.CITY_LATLON[geo.WAREHOUSE_CITY]
    # next stops: where this product is being sent, ranked by demand, with each place's highest-demand SKU
    inc, arr, top_in = {}, {}, {}
    by_pos = {}
    for p in sel:
        by_pos.setdefault(p["store"], []).append(p)
    for a in r["actions"]:
        if a["sku_id"] in ids and a["type"] != "PULL_BACK" and a["destination"] in coords:
            inc[a["destination"]] = inc.get(a["destination"], 0) + a["qty"]
            arr[a["destination"]] = min(arr.get(a["destination"], a["arrival_date"]), a["arrival_date"])
            cur = top_in.get(a["destination"])
            if not cur or a["qty"] > cur["qty"]:
                top_in[a["destination"]] = {"sku": a["sku_id"], "style": a["style_name"], "size": a["size"], "qty": a["qty"]}
    dem = {o["store"]: o["demand_28d"] for o in out}
    stops = []
    for sid, units in inc.items():
        ps = by_pos.get(sid, [])
        best = max(ps, key=lambda p: p["daily"]) if ps else None
        stops.append({"store": sid, "city": stores[sid]["city"], "tier": stores[sid]["tier"], "incoming_units": units, "arrival": arr[sid], "demand_28d": dem.get(sid, 0.0),
                      "top_sku": None if not best else {"sku": best["sku"], "style": best["style_name"], "size": best["size"], "demand_28d": round(best["daily"] * 28, 1), "stock": best["on_hand"] + best["in_transit"]},
                      "top_incoming": top_in.get(sid)})
    stops.sort(key=lambda s: (-s["demand_28d"], s["store"]))
    for i, s in enumerate(stops[:12], 1):
        s["rank"] = i
    return {"stores": out, "moves": moves, "next_stops": stops[:12], "warehouse": {"lat": la, "lon": lo, "on_hand": sum(w["on_hand"] for w in wh), "plan_out": sum(w["plan_out"] for w in wh)},
            "note": "Store positions are approximate city-centre placements (the data has cities, not coordinates)."}


@app.get("/api/backtest")
def backtest():
    b = _result().get("backtest")
    if b:
        return b
    p = Path(__file__).resolve().parents[2] / "docs" / "FORECAST_BACKTEST.json"
    if not p.exists():
        raise HTTPException(404, "back-test not generated yet: run  python -m nodal.cli --backtest")
    return json.loads(p.read_text())


@app.get("/api/export.csv")
def export_csv():
    r = _result()
    dec = db.current_decisions(_state["run_id"])
    cols = ["id", "decision", "urgency", "type_label", "reason_label", "sku_id", "style_name", "category", "size", "source", "destination", "qty", "dispatch_date", "arrival_date", "lead_days",
            "unit_mrp", "value_mrp", "cover_before_days", "cover_after_days", "confidence", "requires_approval", "ai_outcome", "flags", "explanation"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for a in r["actions"]:
        row = {**a, "decision": (dec.get(a["id"]) or {}).get("decision", "pending"), "ai_outcome": _ai_brief(a["id"])["ai_outcome"] or ""}
        w.writerow([";".join(row[c]) if c == "flags" else row.get(c) for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=nodal_{_state['run_id']}.csv"})


# ---------------------------------------------------------------- operator endpoints (token-protected; disabled unless NODAL_ADMIN_TOKEN is set)
def _admin(token: str | None):
    want = os.environ.get("NODAL_ADMIN_TOKEN")
    if not want or token != want:
        raise HTTPException(403, "admin endpoints are disabled or the token is wrong")


@app.put("/api/admin/inbox/{name}")
async def upload(name: str, request: Request, x_admin_token: str | None = Header(default=None)):
    """Method 2: operator uploads one dataset CSV (e.g. stock_snapshot.csv) into the inbox."""
    _admin(x_admin_token)
    from .required import REQUIRED
    base = name[:-4] if name.endswith(".csv") else name
    if base not in REQUIRED and base not in ("rules_extra",):
        raise HTTPException(422, f"unknown dataset '{base}'; expected one of {sorted(REQUIRED)}")
    body = await request.body()
    if not body or len(body) > 60_000_000:
        raise HTTPException(422, "empty or too large")
    d = pipeline.inbox_dir(); d.mkdir(parents=True, exist_ok=True)
    (d / f"{base}.csv").write_bytes(body)
    db.audit("dataset_uploaded", "operator", name=base, bytes=len(body))
    return {"saved": f"{base}.csv", "bytes": len(body), "complete": not pipeline._complete(d)}


@app.post("/api/admin/run")
def admin_run(force: bool = False, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    if MODE == "published":
        raise HTTPException(501, "this deployment does not compute plans; run the pipeline on the operator machine")
    threading.Thread(target=lambda: pipeline.daily(force=force), daemon=True).start()
    return {"started": True}


# ---------------------------------------------------------------- PWA + static
class NoCacheStatic(StaticFiles):
    """Always revalidate (ETag): a new deploy is never masked by a stale cached stylesheet or script."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=FRONTEND), name="static")


@app.get("/manifest.webmanifest")
def manifest_file():
    return FileResponse(FRONTEND / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(FRONTEND / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})
