"""AI layer: client robustness, schema/number/evidence guards, alternatives and deterministic revalidation. No live Ollama needed."""
import copy, json, os, tempfile
import httpx
import pytest
from nodal import db
from nodal.ai import alternatives, review
from nodal.ai.ollama import OllamaClient, OllamaError
from nodal.validate import validate


def client_with(handler, **kw):
    return OllamaClient(base_url="http://ollama.test", model="qwen3:8b", retries=kw.pop("retries", 1), transport=httpx.MockTransport(handler), **kw)


def ok_body(payload):
    return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})


GOOD = {"outcome": "CONFIRM", "plain_explanation": "This store will sell more than it holds, so the move makes sense.", "reasoning": "Cover is low and rules pass.",
        "evidence_refs": ["validator"], "risks": [], "missing_information": [], "confidence": "medium", "preferred_candidate": None}


@pytest.fixture()
def tmpdb(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    db.reset_engine()
    yield
    db.reset_engine()


def test_ollama_returns_structured_json():
    c = client_with(lambda r: ok_body(GOOD))
    assert c.chat_json("s", "u", {"type": "object"})["outcome"] == "CONFIRM"


def test_ollama_timeout_retries_then_raises():
    calls = {"n": 0}
    def h(r):
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")
    with pytest.raises(OllamaError):
        client_with(h, retries=2).chat_json("s", "u", {})
    assert calls["n"] == 3


def test_ollama_malformed_json_is_retried_then_fails():
    c = client_with(lambda r: httpx.Response(200, json={"message": {"content": "not json {"}}), retries=1)
    with pytest.raises(OllamaError):
        c.chat_json("s", "u", {})


def test_ollama_availability_probe():
    up = client_with(lambda r: httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}))
    assert up.available()["ok"]
    missing = client_with(lambda r: httpx.Response(200, json={"models": [{"name": "other:1b"}]}))
    assert missing.available()["error"] == "model_not_installed"
    def boom(r): raise httpx.ConnectError("down")
    assert not client_with(boom).available()["ok"]


def _move(result):
    a = next(x for x in result["actions"] if x["type"] == "WH_TO_STORE")
    return a, a["evidence"]


def _cand(a, delta=100):
    return {"id": "ALT-1", "kind": "alternative_donor", "source": "LKO-02", "destination": a["destination"], "qty": a["qty"], "lead_days": 2,
            "metrics": {"delta_expected_revenue": delta, "source_cover_after_days": 40.0, "dest_cover_after_days": 35.0}, "action": {}, "validation": {"passed": True}}


def test_valid_review_is_accepted_and_keeps_deterministic_note(result):
    a, ev = _move(result)
    out = review.review_one(client_with(lambda r: ok_body(GOOD)), a, ev, [], result["manifest"]["policy"])
    assert out["state"] == "completed" and out["review"]["outcome"] == "CONFIRM"
    assert "not a calibrated" in out["review"]["model_confidence_note"]


def test_hallucinated_quantity_is_rejected(result):
    a, ev = _move(result)
    bad = {**GOOD, "plain_explanation": "The store will sell 987654 units next week, so move now."}
    out = review.review_one(client_with(lambda r: ok_body(bad)), a, ev, [], result["manifest"]["policy"])
    assert out["state"] == "failed" and "ungrounded" in out["error"]


def test_invalid_evidence_reference_is_rejected(result):
    a, ev = _move(result)
    out = review.review_one(client_with(lambda r: ok_body({**GOOD, "evidence_refs": ["made_up_key"]})), a, ev, [], result["manifest"]["policy"])
    assert out["state"] == "failed"
    out = review.review_one(client_with(lambda r: ok_body({**GOOD, "evidence_refs": []})), a, ev, [], result["manifest"]["policy"])
    assert out["state"] == "failed"


def test_malformed_schema_is_rejected(result):
    a, ev = _move(result)
    out = review.review_one(client_with(lambda r: ok_body({"outcome": "MAYBE"})), a, ev, [], result["manifest"]["policy"])
    assert out["state"] == "failed" and "malformed" in out["error"]


def test_alternative_must_be_a_validated_candidate_with_supporting_calculation(result):
    a, ev = _move(result)
    pol = result["manifest"]["policy"]
    alt_resp = {**GOOD, "outcome": "ALTERNATIVE", "preferred_candidate": "ALT-9"}
    out = review.review_one(client_with(lambda r: ok_body(alt_resp)), a, ev, [_cand(a)], pol)
    assert out["review"]["outcome"] == "FLAG" and out["alternative"] is None                      # invented candidate id
    good = {**GOOD, "outcome": "ALTERNATIVE", "preferred_candidate": "ALT-1"}
    out = review.review_one(client_with(lambda r: ok_body(good)), a, ev, [_cand(a, delta=-500)], pol)
    assert out["review"]["outcome"] == "FLAG" and out["alternative"] is None                      # calculation does not support it
    out = review.review_one(client_with(lambda r: ok_body(good)), a, ev, [_cand(a, delta=250)], pol)
    assert out["review"]["outcome"] == "ALTERNATIVE" and out["alternative"]["id"] == "ALT-1"


def test_ai_unavailable_leaves_review_pending_and_does_not_touch_the_plan(result, tmpdb):
    db.create_run("t1", "manual", "2026-10-31", "h")
    acts = result["actions"][:5]
    def down(r): raise httpx.ConnectError("down")
    before = copy.deepcopy(acts)
    out = review.review_run("t1", acts, {a["id"]: a["evidence"] for a in acts}, {}, result["manifest"]["policy"], client=client_with(down), limit=3)
    assert out["ai_status"] == "pending" and out["completed"] == 0
    assert db.get_run("t1")["ai_status"] == "pending" and acts == before
    states = {v["state"] for v in db.reviews_of("t1").values()}
    assert states == {"pending", "skipped"}


def test_review_cache_reuses_unchanged_evidence(result, tmpdb):
    db.create_run("c1", "manual", "d", "h1"); db.create_run("c2", "manual", "d", "h2")
    acts = result["actions"][:2]; ev = {a["id"]: a["evidence"] for a in acts}
    n = {"calls": 0}
    def h(r):
        if r.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        n["calls"] += 1
        return ok_body(GOOD)
    c = client_with(h)
    review.review_run("c1", acts, ev, {}, result["manifest"]["policy"], client=c, limit=2)
    first = n["calls"]
    out = review.review_run("c2", acts, ev, {}, result["manifest"]["policy"], client=c, limit=2)
    assert n["calls"] == first and out["cached"] == 2


def test_numbers_guard_allows_ids_dates_and_given_values():
    f = {"units": 16, "cover": 27.3, "sku": "KUR-04-M", "arrival": "2026-11-07", "value": 115184}
    rv = review.AIReview(outcome="CONFIRM", plain_explanation="Moving 16 units of KUR-04-M arriving 2026-11-07 leaves 27.3 days of cover (about 1.2 lakh).", reasoning="ok", evidence_refs=["units"], confidence="low")
    assert review.ungrounded_numbers(rv, {"facts": f}) == []


def test_alternatives_are_feasible_and_revalidated(result):
    ctx, acts = result["_ctx"], result["actions"]
    cands = alternatives.generate(ctx, acts, acts[:150])
    assert cands, "expected at least one validated alternative on the real plan"
    for mid, lst in cands.items():
        for c in lst:
            assert c["source"] != c["destination"] and c["validation"]["passed"]
            acts2 = [c["action"] if x["id"] == mid else x for x in acts]
            assert not validate(ctx["d"], ctx["vel"], acts2, ctx["seas"])["violations"]      # independent re-check here too
            assert c["action"]["qty"] == next(x for x in acts if x["id"] == mid)["qty"]       # no invented quantities


def test_validator_blocks_a_forged_alternative(result):
    ctx, acts = result["_ctx"], result["actions"]
    a = next(x for x in acts if x["type"] == "WH_TO_STORE")
    forged = {**a, "qty": 10 ** 6, "value_mrp": 10 ** 9, "requires_approval": True}
    res = validate(ctx["d"], ctx["vel"], [forged if x["id"] == a["id"] else x for x in acts], ctx["seas"])
    assert {v["rule"] for v in res["violations"]} & {"supply", "capacity"}
