"""Shipments (one decision per store), cached plain summaries, batch decisions with one confirmation for big items."""
import importlib, json
from pathlib import Path
import httpx, pytest
from fastapi.testclient import TestClient
from nodal import db, pipeline, shipments as shp
from nodal.ai.ollama import OllamaClient

BUNDLE = Path(__file__).resolve().parents[1] / "published" / "latest.json.gz"
pytestmark = pytest.mark.skipif(not BUNDLE.exists(), reason="no published bundle")


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db"); monkeypatch.setenv("NODAL_BUNDLE", str(BUNDLE))
    db.reset_engine()
    import nodal.api as m
    importlib.reload(m)
    yield TestClient(m.app)
    db.reset_engine()


def _built():
    b = pipeline.load_bundle()
    return b, shp.build(b["actions"], b["stores"], b["manifest"]["seasonality"], {})


def test_every_move_is_in_exactly_one_shipment_and_totals_match():
    b, sh = _built()
    ids = [m for s in sh for m in s["move_ids"]]
    assert sorted(ids) == sorted(a["id"] for a in b["actions"]) and len(ids) == len(set(ids))
    assert sum(s["units"] for s in sh) == sum(a["qty"] for a in b["actions"])
    assert len(sh) < len(b["actions"]) / 10                           # far fewer decisions than moves


def test_pullbacks_group_by_source_and_deliveries_by_destination():
    b, sh = _built()
    by = {a["id"]: a for a in b["actions"]}
    for s in sh:
        for m in s["move_ids"]:
            a = by[m]
            assert (a["source"] if s["kind"] == "pullback" else a["destination"]) == s["store"]
            assert (a["type"] == "PULL_BACK") == (s["kind"] == "pullback")


def test_template_summary_uses_only_facts_and_no_jargon():
    _, sh = _built()
    for s in sh:
        t = shp.template_summary(s)
        assert shp._grounded(t, {**shp.facts_for(s), "n": s["n_moves"], "latest": s["latest_arrival"]}), t


def test_ai_summary_is_checked_then_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/c.db"); db.reset_engine()
    _, sh = _built(); sh = sh[:2]
    calls = {"n": 0}
    def h(r):
        if r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        calls["n"] += 1
        facts = json.loads(json.loads(r.content)["messages"][1]["content"].split("\n")[0])
        text = f"{facts['store']} is getting {facts['units']} units soon." if calls["n"] > 2 else "Exactly 987654 units will arrive."   # first two calls invent a number
        return httpx.Response(200, json={"message": {"content": json.dumps({"summary": text})}})
    c = OllamaClient(base_url="http://x.test", model="qwen3:8b", retries=0, transport=httpx.MockTransport(h))
    st = shp.summarise(sh, c)
    assert st["local_ai"] + st["template"] == 2 and all("987654" not in s["summary"] for s in sh)     # invented numbers never shown
    before = calls["n"]
    sh2 = [dict(s) for s in sh]
    st2 = shp.summarise(sh2, c)
    assert st2["cached"] == st["local_ai"] and calls["n"] <= before + st["template"] * 2        # cached summaries are not regenerated
    db.reset_engine()


def test_batch_approve_needs_one_confirmation_for_big_items_and_can_be_undone(api):
    s = next(x for x in api.get("/api/shipments").json() if x["needs_signoff"] > 0)
    r = api.post("/api/decisions/batch", json={"shipment_id": s["id"], "decision": "approved"})
    assert r.status_code == 409 and r.json()["detail"]["big_items"] == s["needs_signoff"]
    ok = api.post("/api/decisions/batch", json={"shipment_id": s["id"], "decision": "approved", "confirm_high_value": True}, headers={"X-Client-Id": "t"})
    assert ok.status_code == 200 and ok.json()["written"] == s["n_moves"] and "does not dispatch" in ok.json()["note"]
    assert api.get(f"/api/shipments/{s['id']}").json()["status"] == "approved"
    again = api.post("/api/decisions/batch", json={"shipment_id": s["id"], "decision": "approved", "confirm_high_value": True}).json()
    assert again["written"] == 0                                       # idempotent: no duplicate approvals
    undo = api.post("/api/decisions/batch", json={"shipment_id": s["id"], "decision": "cleared"}).json()
    assert undo["written"] == s["n_moves"] and api.get(f"/api/shipments/{s['id']}").json()["status"] == "pending"
    hist = api.get("/api/history").json()["decisions"]
    assert len(hist) >= 2 * s["n_moves"]                               # approve and undo are both in the audit log


def test_routine_approval_skips_flagged_shipments(api):
    sh = api.get("/api/shipments").json()
    assert all(not (s["routine"] and (s["ai_flagged"] or s["low_confidence"])) for s in sh)
    r = api.post("/api/decisions/routine", json={"confirm_high_value": True}).json()
    assert r["moves"] == sum(s["n_moves"] for s in sh if s["routine"])
