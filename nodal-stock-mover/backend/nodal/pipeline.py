"""Daily autonomous pipeline: ingest -> validate -> snapshot -> forecast/allocate/validate -> candidates -> AI review -> publish -> notify.

Idempotent (same input hash is never published twice), stage-recorded, retryable, and AI-optional: if Ollama is down the
deterministic plan still completes and the run is marked ai_status=pending.
"""
from __future__ import annotations
import datetime as dt, gzip, hashlib, json, logging, os, shutil, time, uuid
from pathlib import Path

from . import db
from .loader import REQUIRED, DataError, load_all

log = logging.getLogger("nodal.pipeline")
ROOT = Path(__file__).resolve().parents[3]
APP = Path(__file__).resolve().parents[2]
SUPPLIED = ROOT / "manyavar-vch3-data" / "csv"
def published_dir() -> Path:
    return Path(os.environ.get("NODAL_PUBLISHED", str(APP / "published")))


def cfg(name, default):
    return os.environ.get(name, default)


def inbox_dir() -> Path:
    return Path(cfg("NODAL_INBOX", str(APP / "data" / "inbox")))


def snapshot_root() -> Path:
    return Path(cfg("NODAL_SNAPSHOTS", str(APP / "data" / "snapshots")))


def _complete(d: Path) -> list[str]:
    return [n for n in REQUIRED if not (d / f"{n}.csv").exists()]


def resolve_dataset() -> tuple[Path, str]:
    """Method 1 (watched folder): newest complete dataset in the inbox (a folder of CSVs, or CSVs dropped in the root);
    otherwise the configured fallback directory. Returns (dir, source_label)."""
    inbox = inbox_dir()
    cands = []
    if inbox.is_dir():
        for p in [inbox, *[x for x in inbox.iterdir() if x.is_dir()]]:
            if _complete(p):                      # _complete() lists MISSING files: skip incomplete sets
                continue
            cands.append((max((p / f"{n}.csv").stat().st_mtime for n in REQUIRED), p))
    if cands:
        return sorted(cands)[-1][1], "inbox"
    fb = Path(cfg("NODAL_DATA_DIR", str(SUPPLIED)))
    return fb, "configured_dir"


def input_hash(d: Path) -> str:
    h = hashlib.sha256()
    for n in sorted(REQUIRED):
        h.update(n.encode()); h.update(hashlib.sha256((d / f"{n}.csv").read_bytes()).digest())
    return h.hexdigest()


class Stages:
    def __init__(self, run_id):
        self.run_id, self.s = run_id, {}

    def run(self, name, fn, retries=1):
        self.s[name] = {"status": "running", "started": db.now(), "attempts": 0}
        db.update_run(self.run_id, stages=self.s)
        last = None
        for attempt in range(retries + 1):
            self.s[name]["attempts"] = attempt + 1
            t0 = time.time()
            try:
                out = fn()
                self.s[name].update(status="completed", seconds=round(time.time() - t0, 1), finished=db.now())
                db.update_run(self.run_id, stages=self.s)
                return out
            except DataError:
                last = None
                self.s[name].update(status="failed", finished=db.now())
                db.update_run(self.run_id, stages=self.s)
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                log.exception("stage %s attempt %d failed", name, attempt + 1)
        self.s[name].update(status="failed", error=f"{type(last).__name__}: {last}"[:300], finished=db.now())
        db.update_run(self.run_id, stages=self.s)
        raise last


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def mark_stale_runs(max_age_hours=3):
    """Recovery after an interrupted process: runs stuck in 'running' are closed as failed (never silently resumed half-done)."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
    for r in db.list_runs(200):
        if r["status"] == "running" and r["created_at"] < cutoff:
            db.update_run(r["run_id"], status="failed", error="interrupted: process stopped before completion")
            db.audit("run_marked_interrupted", detail_run=r["run_id"])


def daily(force=False, with_ai=True, kind="daily", dataset: Path | None = None, notify=True) -> dict:
    mark_stale_runs()
    src_dir, src_label = (dataset, "explicit") if dataset else resolve_dataset()
    missing = _complete(Path(src_dir))
    run_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    if missing:
        db.create_run(run_id, kind, None, None, status="failed")
        db.update_run(run_id, error=f"missing required datasets: {missing}", stages={"ingest": {"status": "failed", "error": f"missing {missing}"}})
        db.audit("run_failed", detail_run=run_id, reason="missing_files", missing=missing)
        return {"run_id": run_id, "status": "failed", "error": f"missing required datasets: {missing}"}
    ih = input_hash(Path(src_dir))
    prior = db.find_run_by_hash(ih)
    if prior and not force:
        db.audit("duplicate_input_skipped", detail_run=prior["run_id"], input_hash=ih)
        return {"run_id": prior["run_id"], "status": "skipped_duplicate", "message": "this exact dataset was already processed; nothing republished", "input_hash": ih}
    db.create_run(run_id, kind, None, ih)
    st = Stages(run_id)
    try:
        d = st.run("ingest_validate", lambda: load_all(src_dir))
        db.update_run(run_id, as_of=str(d.as_of.date()))

        def snap():
            dst = snapshot_root() / run_id
            dst.mkdir(parents=True, exist_ok=True)                       # never overwrites an earlier snapshot (unique run_id)
            for n in REQUIRED:
                shutil.copy2(Path(src_dir) / f"{n}.csv", dst / f"{n}.csv")
            (dst / "manifest.json").write_text(json.dumps({"run_id": run_id, "input_hash": ih, "source": src_label, "files": d.manifest_files, "created": db.now()}, indent=1))
            return str(dst)
        st.run("snapshot", snap)

        from .engine import run as engine_run
        res = st.run("forecast_allocate_validate", lambda: engine_run(str(src_dir)))
        ctx = res["_ctx"]
        db.store_moves(run_id, res["actions"])
        from .ai import alternatives, review
        cand = st.run("alternative_candidates", lambda: alternatives.generate(ctx, res["actions"], res["actions"]), retries=0)
        ai_summary = {"ai_status": "skipped"}
        if with_ai:
            ev = {a["id"]: a["evidence"] for a in res["actions"]}
            ai_summary = st.run("ai_review", lambda: review.review_run(run_id, res["actions"], ev, cand, res["manifest"]["policy"]), retries=0)
        reviews = db.reviews_of(run_id)
        for a in res["actions"]:
            reviews.setdefault(a["id"], None)
        bundle = st.run("publish", lambda: publish(run_id, res, reviews, cand, ih, src_label, st.s))
        n_val = sum(1 for r in res["validation"]["rules"] if not r["passed"])
        status = "completed" if ai_summary.get("ai_status") in ("completed", "skipped") else "partial"
        metrics = {"moves": len(res["actions"]), "units": res["summary"]["total"]["units"], "value_mrp": res["summary"]["total"]["value_mrp"], "needs_approval": res["summary"]["needs_approval"],
                   "rules_failed": n_val, "ai": ai_summary, "alternatives_found": sum(1 for r in reviews.values() if r and r.get("alternative")),
                   "dq_warnings": sum(1 for f in res["audit"]["findings"] if f["severity"] in ("warn", "error")), "runtime_seconds": res["manifest"]["runtime_seconds"]}
        db.update_run(run_id, status=status, ai_status=ai_summary.get("ai_status", "skipped"), metrics=metrics, stages=st.s)
        db.audit("run_completed", detail_run=run_id, status=status, **{k: metrics[k] for k in ("moves", "units")})
        if notify:
            notify_operator(run_id, status, metrics)
        return {"run_id": run_id, "status": status, "metrics": metrics, "bundle": bundle}
    except Exception as e:  # noqa: BLE001
        db.update_run(run_id, status="failed", error=f"{type(e).__name__}: {e}"[:400], stages=st.s)
        db.audit("run_failed", detail_run=run_id, error=str(e)[:200])
        return {"run_id": run_id, "status": "failed", "error": str(e)[:400]}


def publish(run_id, res, reviews, cand, ih, src_label, stages):
    """Write the immutable run bundle (gzip JSON) that the public viewer serves. Previous bundles are kept."""
    PUBLISHED = published_dir()
    PUBLISHED.mkdir(parents=True, exist_ok=True)
    bt = APP / "docs" / "FORECAST_BACKTEST.json"
    rev = {}
    for mid, r in reviews.items():
        if r:
            rev[mid] = {k: r[k] for k in ("state", "outcome", "review", "alternative", "error", "model", "updated_at")}
    bundle = {"run_id": run_id, "created_at": db.now(), "input_hash": ih, "source": src_label, "stages": stages,
              **{k: res[k] for k in ("manifest", "audit", "summary", "actions", "validation", "excluded", "blocked_samples", "unresolved", "stores", "positions", "warehouse")},
              "reviews": rev, "backtest": json.loads(bt.read_text()) if bt.exists() else None}
    raw = json.dumps(bundle, default=_json_default, separators=(",", ":")).encode()
    for name in (f"run_{run_id}.json.gz", "latest.json.gz"):
        with gzip.open(PUBLISHED / name, "wb", compresslevel=6) as f:
            f.write(raw)
    return {"file": f"published/run_{run_id}.json.gz", "bytes": len(raw)}


def load_bundle(path: Path | None = None) -> dict | None:
    p = path or (published_dir() / "latest.json.gz")
    if not p.exists():
        return None
    with gzip.open(p, "rb") as f:
        return json.loads(f.read())


def import_bundle_to_db(b: dict):
    """Make a published bundle's run, moves and AI reviews available to the server-side decision store (idempotent)."""
    if db.get_run(b["run_id"]):
        return
    db.create_run(b["run_id"], "publish", b["manifest"]["as_of"], b.get("input_hash"), status="completed")
    db.store_moves(b["run_id"], b["actions"])
    for mid, r in (b.get("reviews") or {}).items():
        db.set_review(b["run_id"], mid, r["state"], outcome=r.get("outcome"), review=r.get("review"), alternative=r.get("alternative"), error=r.get("error"), model=r.get("model"))
    ai = [r["state"] for r in (b.get("reviews") or {}).values()]
    db.update_run(b["run_id"], ai_status="completed" if "completed" in ai else "pending", stages=b.get("stages") or {},
                  metrics={"moves": len(b["actions"]), "units": b["summary"]["total"]["units"], "value_mrp": b["summary"]["total"]["value_mrp"], "needs_approval": b["summary"]["needs_approval"]})


def notify_operator(run_id, status, metrics):
    msg = f"NODAL run {run_id}: {status}; {metrics['moves']} moves, {metrics['units']} units, AI {metrics['ai'].get('ai_status')}"
    log.info(msg)
    p = Path(cfg("NODAL_NOTIFY_FILE", str(APP / "data" / "notifications.log")))
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(f"{db.now()} {msg}\n")
    url = os.environ.get("NODAL_NOTIFY_WEBHOOK")
    if url:
        try:
            import httpx
            httpx.post(url, json={"text": msg}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("webhook notify failed: %s", e)


def seconds_until(hhmm: str, tz: str, now: dt.datetime | None = None) -> float:
    from zoneinfo import ZoneInfo
    z = ZoneInfo(tz)
    now = (now or dt.datetime.now(z)).astimezone(z)
    h, m = map(int, hhmm.split(":"))
    nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if nxt <= now:
        nxt += dt.timedelta(days=1)
    return (nxt - now).total_seconds()


def schedule_forever():
    tz, hhmm = cfg("NODAL_TZ", "Asia/Kolkata"), cfg("NODAL_DAILY_TIME", "06:00")
    log.info("scheduler: daily at %s %s", hhmm, tz)
    while True:
        time.sleep(seconds_until(hhmm, tz))
        out = daily()
        log.info("scheduled run: %s %s", out.get("run_id"), out.get("status"))


def watch_inbox(poll_seconds=30):
    """Method 1: poll the watched folder; run the pipeline whenever a new complete dataset appears (duplicates are skipped by hash)."""
    seen = None
    while True:
        try:
            d, label = resolve_dataset()
            if label == "inbox" and not _complete(d):
                h = input_hash(d)
                if h != seen:
                    seen = h
                    out = daily(dataset=d)
                    log.info("inbox run: %s %s", out.get("run_id"), out.get("status"))
        except Exception:  # noqa: BLE001
            log.exception("watch loop error")
        time.sleep(poll_seconds)
