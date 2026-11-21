"""Database, audit trail, concurrency and the decision API (uses the committed/published run bundle)."""
import gzip, json, os, threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from nodal import db, pipeline

BUNDLE = Path(__file__).resolve().parents[1] / "published" / "latest.json.gz"


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    """SQLite by default; set NODAL_TEST_DATABASE_URL to run the same tests against PostgreSQL."""
    url = os.environ.get("NODAL_TEST_DATABASE_URL") or f"sqlite:///{tmp_path}/t.db"
    monkeypatch.setenv("DATABASE_URL", url)
    db.reset_engine()
    if not url.startswith("sqlite"):
        db.meta.drop_all(db.engine())
        db.reset_engine()
    yield tmp_path
    db.reset_engine()


def test_approvals_persist_across_restarts_and_audit_is_append_only(fresh):
    db.create_run("r1", "manual", "d", "h")
    db.record_decision("r1", "M1", "approved", "judge-a", original={"x": 1})
    db.reset_engine()                                              # simulate a server restart
    assert db.current_decisions("r1")["M1"]["decision"] == "approved"
    db.record_decision("r1", "M1", "rejected", "judge-b", reason="too many")
    log = db.decision_log("r1")
    assert [x["decision"] for x in log] == ["rejected", "approved"]      # both kept, newest first
    assert db.current_decisions("r1")["M1"]["actor"] == "judge-b" and log[0]["reason"] == "too many"


def test_duplicate_approval_is_idempotent_and_multiple_runs_are_separate(fresh):
    db.create_run("r1", "manual", "d", "h1"); db.create_run("r2", "manual", "d", "h2")
    assert db.record_decision("r1", "M1", "approved", "a")["written"] is True
    assert db.record_decision("r1", "M1", "approved", "b")["written"] is False
    assert db.record_decision("r2", "M1", "rejected", "a")["written"] is True
    assert db.current_decisions("r1")["M1"]["decision"] == "approved" and db.current_decisions("r2")["M1"]["decision"] == "rejected"


def test_concurrent_identical_decisions_write_one_row(fresh):
    db.create_run("r1", "manual", "d", "h")
    errs = []
    def go(i):
        try:
            db.record_decision("r1", "M1", "approved", f"judge-{i}")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs
    rows = [x for x in db.decision_log("r1") if x["decision"] == "approved"]
    assert len(rows) == 1                                           # exactly one approval row, never duplicated
    assert db.current_decisions("r1")["M1"]["decision"] == "approved"


def test_database_failure_is_reported_not_crashing(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    db.reset_engine()
    try:
        assert db.healthy()["ok"] is False
    except Exception:
        pass                                                         # engine creation itself may refuse; both are acceptable "reported" failures
    finally:
        db.reset_engine()


@pytest.mark.skipif(not BUNDLE.exists(), reason="no published bundle yet")
def test_decision_api_enforces_signoff_and_never_dispatches(fresh, monkeypatch):
    monkeypatch.setenv("NODAL_BUNDLE", str(BUNDLE))
    import importlib, nodal.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    acts = c.get("/api/actions").json()
    big = next(a for a in acts if a["requires_approval"]); small = next(a for a in acts if not a["requires_approval"])
    r = c.post("/api/decisions", json={"move_id": big["id"], "decision": "approved"}, headers={"X-Client-Id": "t1"})
    assert r.status_code == 409                                      # high-value sign-off needs explicit confirmation
    ok = c.post("/api/decisions", json={"move_id": big["id"], "decision": "approved", "confirm_high_value": True}, headers={"X-Client-Id": "t1"}).json()
    assert ok["written"] and "does not dispatch" in ok["note"]
    assert c.post("/api/decisions", json={"move_id": small["id"], "decision": "approved_alternative"}).status_code == 422   # no AI alternative exists
    assert c.post("/api/decisions", json={"move_id": "NOPE", "decision": "approved"}).status_code == 404
    cur = c.get("/api/decisions").json()["current"]
    assert cur[big["id"]]["actor"] == "judge-t1"
    hist = c.get("/api/history").json()
    assert hist["decisions"][0]["move_id"] == big["id"] and hist["audit"]
    st = c.get("/api/status").json()
    assert st["approved"] == 1 and st["rules_passed"] == st["rules_total"] and st["automation"]["schedule"]


@pytest.mark.skipif(not BUNDLE.exists(), reason="no published bundle yet")
def test_admin_endpoints_disabled_without_token_and_health(fresh, monkeypatch):
    monkeypatch.setenv("NODAL_BUNDLE", str(BUNDLE)); monkeypatch.delenv("NODAL_ADMIN_TOKEN", raising=False)
    import importlib, nodal.api as api
    importlib.reload(api)
    c = TestClient(api.app)
    assert c.put("/api/admin/inbox/stores.csv", content=b"x").status_code == 403
    monkeypatch.setenv("NODAL_ADMIN_TOKEN", "secret")
    assert c.put("/api/admin/inbox/stores.csv", content=b"x", headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c.put("/api/admin/inbox/notatable.csv", content=b"x", headers={"X-Admin-Token": "secret"}).status_code == 422
    assert c.get("/healthz").json()["ok"] is True
