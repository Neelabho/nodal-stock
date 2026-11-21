"""Persistence: runs, published moves, AI reviews/alternatives, append-only decisions and audit events.

SQLite for local development, PostgreSQL in production: set DATABASE_URL (e.g. postgresql+psycopg://user:pass@host/db).
Schema changes go through the small versioned migration list below (applied once, recorded in schema_migrations).
"""
from __future__ import annotations
import datetime as dt, hashlib, json, os, threading
from pathlib import Path
import sqlalchemy as sa

DEFAULT_URL = "sqlite:///" + str(Path(__file__).resolve().parents[2] / "data" / "nodal.db")
meta = sa.MetaData()

runs = sa.Table("runs", meta,
    sa.Column("run_id", sa.String(40), primary_key=True), sa.Column("created_at", sa.String(32), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),                   # daily | manual | publish
    sa.Column("as_of", sa.String(12)), sa.Column("input_hash", sa.String(64), index=True),
    sa.Column("status", sa.String(24), nullable=False),                 # running | completed | partial | failed
    sa.Column("ai_status", sa.String(24), nullable=False, server_default="pending"),
    sa.Column("metrics", sa.JSON), sa.Column("stages", sa.JSON), sa.Column("error", sa.Text))
moves = sa.Table("moves", meta,
    sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.run_id"), primary_key=True), sa.Column("move_id", sa.String(16), primary_key=True),
    sa.Column("fingerprint", sa.String(40), index=True), sa.Column("payload", sa.JSON, nullable=False))
ai_reviews = sa.Table("ai_reviews", meta,
    sa.Column("run_id", sa.String(40), primary_key=True), sa.Column("move_id", sa.String(16), primary_key=True),
    sa.Column("state", sa.String(16), nullable=False),                  # pending | processing | completed | failed | skipped
    sa.Column("evidence_hash", sa.String(64), index=True), sa.Column("outcome", sa.String(24)), sa.Column("review", sa.JSON),
    sa.Column("alternative", sa.JSON), sa.Column("error", sa.Text), sa.Column("model", sa.String(64)), sa.Column("updated_at", sa.String(32)))
decisions = sa.Table("decisions", meta,                                 # append-only: never updated or deleted
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("ts", sa.String(32), nullable=False),
    sa.Column("run_id", sa.String(40), nullable=False, index=True), sa.Column("move_id", sa.String(16), nullable=False, index=True),
    sa.Column("decision", sa.String(24), nullable=False),               # approved | approved_alternative | rejected | skipped | cleared
    sa.Column("actor", sa.String(64), nullable=False), sa.Column("reason", sa.Text),
    sa.Column("original", sa.JSON), sa.Column("ai_alternative", sa.JSON), sa.Column("confirmed_high_value", sa.Boolean, server_default=sa.false()))
audit_events = sa.Table("audit_events", meta,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("ts", sa.String(32), nullable=False),
    sa.Column("actor", sa.String(64)), sa.Column("event", sa.String(48), nullable=False), sa.Column("detail", sa.JSON))
outcomes = sa.Table("outcomes", meta,                                   # foundation for learning from results (filled when actuals are supplied)
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("run_id", sa.String(40), index=True), sa.Column("move_id", sa.String(16)),
    sa.Column("recorded_at", sa.String(32)), sa.Column("actual_moved", sa.Integer), sa.Column("units_sold_after", sa.Integer),
    sa.Column("remaining_stock", sa.Integer), sa.Column("forecast_units", sa.Float), sa.Column("note", sa.Text))
ai_text = sa.Table("ai_text", meta,                                       # cached plain-language summaries (key = hash of facts + prompt + model)
    sa.Column("key", sa.String(64), primary_key=True), sa.Column("kind", sa.String(24), nullable=False), sa.Column("text", sa.Text, nullable=False),
    sa.Column("model", sa.String(64)), sa.Column("created_at", sa.String(32)))
qa_cache = sa.Table("qa_cache", meta,                                    # cached answers to user questions
    sa.Column("qhash", sa.String(64), primary_key=True), sa.Column("question", sa.Text, nullable=False), sa.Column("answer", sa.Text, nullable=False),
    sa.Column("source", sa.String(24), nullable=False), sa.Column("model", sa.String(64)), sa.Column("hits", sa.Integer, server_default="0"),
    sa.Column("created_at", sa.String(32)), sa.Column("last_hit", sa.String(32)))
qa_usage = sa.Table("qa_usage", meta,                                    # rate-limiting ledger
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True), sa.Column("ts", sa.String(32), nullable=False, index=True),
    sa.Column("client", sa.String(64), index=True), sa.Column("route", sa.String(12)), sa.Column("cached", sa.Boolean))
schema_migrations = sa.Table("schema_migrations", meta, sa.Column("version", sa.Integer, primary_key=True), sa.Column("applied_at", sa.String(32)))

# version -> tables created. Add a new entry (never edit an old one) for every future schema change.
MIGRATIONS = {1: [runs, moves, ai_reviews, decisions, audit_events, outcomes], 2: [ai_text, qa_cache, qa_usage]}

_engine = None
_lock = threading.Lock()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def engine() -> sa.Engine:
    global _engine
    with _lock:
        if _engine is None:
            url = os.environ.get("DATABASE_URL", DEFAULT_URL)
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url[len("postgres://"):]
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://"):]
            if url.startswith("sqlite:///"):
                Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
            _engine = sa.create_engine(url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
            migrate(_engine)
        return _engine


def reset_engine():
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def migrate(eng: sa.Engine):
    schema_migrations.create(eng, checkfirst=True)
    with eng.begin() as c:
        done = {r[0] for r in c.execute(sa.select(schema_migrations.c.version))}
        for v in sorted(MIGRATIONS):
            if v not in done:
                for t in MIGRATIONS[v]:
                    t.create(c, checkfirst=True)
                c.execute(sa.insert(schema_migrations).values(version=v, applied_at=now()))


def healthy() -> dict:
    try:
        with engine().connect() as c:
            c.execute(sa.text("select 1"))
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def audit(event: str, actor: str | None = None, **detail):
    with engine().begin() as c:
        c.execute(sa.insert(audit_events).values(ts=now(), actor=actor, event=event, detail=detail))


# ---------------------------------------------------------------- runs & moves
def fingerprint(a: dict) -> str:
    """Stable identity of a recommendation across runs (same SKU, route, quantity, reason) so repeats are never duplicated."""
    s = f"{a['type']}|{a['reason']}|{a['sku_id']}|{a['source']}|{a['destination']}|{a['qty']}|{a['dispatch_date']}"
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def create_run(run_id: str, kind: str, as_of: str | None, input_hash: str | None, status="running") -> None:
    with engine().begin() as c:
        c.execute(sa.insert(runs).values(run_id=run_id, created_at=now(), kind=kind, as_of=as_of, input_hash=input_hash, status=status, ai_status="pending", stages={}))


def update_run(run_id: str, **vals):
    with engine().begin() as c:
        c.execute(sa.update(runs).where(runs.c.run_id == run_id).values(**vals))


def get_run(run_id: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(sa.select(runs).where(runs.c.run_id == run_id)).mappings().first()
    return dict(r) if r else None


def list_runs(limit=30) -> list[dict]:
    with engine().connect() as c:
        return [dict(r) for r in c.execute(sa.select(runs).order_by(runs.c.created_at.desc()).limit(limit)).mappings()]


def latest_run(statuses=("completed", "partial")) -> dict | None:
    with engine().connect() as c:
        r = c.execute(sa.select(runs).where(runs.c.status.in_(statuses)).order_by(runs.c.created_at.desc()).limit(1)).mappings().first()
    return dict(r) if r else None


def find_run_by_hash(input_hash: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(sa.select(runs).where(runs.c.input_hash == input_hash, runs.c.status.in_(("completed", "partial", "running"))).order_by(runs.c.created_at.desc()).limit(1)).mappings().first()
    return dict(r) if r else None


def store_moves(run_id: str, actions: list[dict]):
    rows = [{"run_id": run_id, "move_id": a["id"], "fingerprint": fingerprint(a), "payload": {k: v for k, v in a.items() if k != "evidence"}} for a in actions]
    with engine().begin() as c:
        c.execute(sa.delete(moves).where(moves.c.run_id == run_id))
        if rows:
            c.execute(sa.insert(moves), rows)


def moves_of(run_id: str) -> list[dict]:
    with engine().connect() as c:
        return [r["payload"] for r in c.execute(sa.select(moves.c.payload).where(moves.c.run_id == run_id).order_by(moves.c.move_id)).mappings()]


# ---------------------------------------------------------------- decisions (append-only, idempotent)
def current_decisions(run_id: str) -> dict:
    """Latest decision per move (the log itself is never modified)."""
    with engine().connect() as c:
        rows = c.execute(sa.select(decisions).where(decisions.c.run_id == run_id).order_by(decisions.c.id)).mappings().all()
    cur = {}
    for r in rows:
        if r["decision"] == "cleared":
            cur.pop(r["move_id"], None)
        else:
            cur[r["move_id"]] = {"decision": r["decision"], "actor": r["actor"], "ts": r["ts"], "reason": r["reason"]}
    return cur


_write_lock = threading.Lock()


def record_decision(run_id: str, move_id: str, decision: str, actor: str, reason: str | None = None, original=None, ai_alternative=None, confirmed=False) -> dict:
    """Append a decision atomically. If the move already has this identical current decision nothing is written (no duplicate approvals),
    even under concurrent requests: one in-process lock plus a PostgreSQL advisory lock per move for multi-process deployments."""
    with _write_lock, engine().begin() as c:
        if c.dialect.name == "postgresql":
            c.execute(sa.text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{run_id}|{move_id}"})
        last = c.execute(sa.select(decisions.c.decision, decisions.c.actor, decisions.c.ts, decisions.c.reason).where(decisions.c.run_id == run_id, decisions.c.move_id == move_id)
                         .order_by(decisions.c.id.desc()).limit(1)).mappings().first()
        cur = dict(last) if last and last["decision"] != "cleared" else None
        if cur and cur["decision"] == decision and decision != "cleared":
            return {"written": False, "current": cur}
        if not cur and decision == "cleared":
            return {"written": False, "current": None}
        c.execute(sa.insert(decisions).values(ts=now(), run_id=run_id, move_id=move_id, decision=decision, actor=actor, reason=reason, original=original,
                                              ai_alternative=ai_alternative, confirmed_high_value=bool(confirmed)))
    return {"written": True, "current": current_decisions(run_id).get(move_id)}


def decision_log(run_id: str | None = None, limit=200) -> list[dict]:
    q = sa.select(decisions).order_by(decisions.c.id.desc()).limit(limit)
    if run_id:
        q = q.where(decisions.c.run_id == run_id)
    with engine().connect() as c:
        return [dict(r) for r in c.execute(q).mappings()]


# ---------------------------------------------------------------- AI reviews
def set_review(run_id: str, move_id: str, state: str, **vals):
    vals = {"state": state, "updated_at": now(), **vals}
    with engine().begin() as c:
        if c.execute(sa.select(ai_reviews.c.move_id).where(ai_reviews.c.run_id == run_id, ai_reviews.c.move_id == move_id)).first():
            c.execute(sa.update(ai_reviews).where(ai_reviews.c.run_id == run_id, ai_reviews.c.move_id == move_id).values(**vals))
        else:
            c.execute(sa.insert(ai_reviews).values(run_id=run_id, move_id=move_id, **vals))


def reviews_of(run_id: str) -> dict:
    with engine().connect() as c:
        return {r["move_id"]: dict(r) for r in c.execute(sa.select(ai_reviews).where(ai_reviews.c.run_id == run_id)).mappings()}


def cached_review(evidence_hash: str, model: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(sa.select(ai_reviews).where(ai_reviews.c.evidence_hash == evidence_hash, ai_reviews.c.state == "completed", ai_reviews.c.model == model).limit(1)).mappings().first()
    return dict(r) if r else None


# ---------------------------------------------------------------- text cache + Q&A cache + usage
def get_text(key: str) -> str | None:
    with engine().connect() as c:
        r = c.execute(sa.select(ai_text.c.text).where(ai_text.c.key == key)).first()
    return r[0] if r else None


def put_text(key: str, kind: str, text: str, model: str):
    with engine().begin() as c:
        if not c.execute(sa.select(ai_text.c.key).where(ai_text.c.key == key)).first():
            c.execute(sa.insert(ai_text).values(key=key, kind=kind, text=text, model=model, created_at=now()))


def qa_get(qhash: str) -> dict | None:
    with engine().begin() as c:
        r = c.execute(sa.select(qa_cache).where(qa_cache.c.qhash == qhash)).mappings().first()
        if r:
            c.execute(sa.update(qa_cache).where(qa_cache.c.qhash == qhash).values(hits=(r["hits"] or 0) + 1, last_hit=now()))
    return dict(r) if r else None


def qa_put(qhash: str, question: str, answer: str, source: str, model: str):
    with engine().begin() as c:
        if not c.execute(sa.select(qa_cache.c.qhash).where(qa_cache.c.qhash == qhash)).first():
            c.execute(sa.insert(qa_cache).values(qhash=qhash, question=question, answer=answer, source=source, model=model, hits=0, created_at=now(), last_hit=now()))


def qa_log(client: str, route: str, cached: bool):
    with engine().begin() as c:
        c.execute(sa.insert(qa_usage).values(ts=now(), client=client, route=route, cached=cached))


def qa_count(since_iso: str, client: str | None = None, uncached_only=False) -> int:
    q = sa.select(sa.func.count()).select_from(qa_usage).where(qa_usage.c.ts >= since_iso)
    if client:
        q = q.where(qa_usage.c.client == client)
    if uncached_only:
        q = q.where(qa_usage.c.cached == sa.false())
    with engine().connect() as c:
        return c.execute(q).scalar() or 0
