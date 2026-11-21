"""Local AI first, Sarvam when the local model is unavailable; all safety checks apply to whichever model answers. Everything is mocked."""
import json
import httpx, pytest
from nodal import db, qa, shipments as shp
from nodal.ai import review
from nodal.ai.ollama import OllamaClient, OllamaError
from nodal.ai.provider import AIProvider


class Log:
    def __init__(self): self.local, self.remote = [], []


def build(log, local_up=True, remote_status=200, remote_text='{"summary": "ok"}', local_text='{"summary": "from local"}', allow_remote=True, key="test-key"):
    def local(r):
        if r.url.path == "/api/tags":
            if not local_up:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        log.local.append(json.loads(r.content))
        if not local_up:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"message": {"content": local_text}})
    def remote(r):
        log.remote.append(json.loads(r.content))
        if remote_status != 200:
            return httpx.Response(remote_status, json={"error": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": remote_text}}]})
    lc = OllamaClient(base_url="http://l.test", model="qwen3:8b", retries=0, transport=httpx.MockTransport(local))
    rc = qa.SarvamClient(api_key=key, model="sarvam-x", transport=httpx.MockTransport(remote))
    return AIProvider(local=lc, remote=rc, allow_remote=allow_remote)


SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/p.db"); db.reset_engine()
    yield
    db.reset_engine()


def test_local_is_used_first_and_sarvam_is_not_called():
    log = Log(); p = build(log)
    assert p.chat_json("s", "u", SCHEMA)["summary"] == "from local"
    assert log.remote == [] and p.last_source == "local_ai" and p.model == "qwen3:8b"


def test_sarvam_takes_over_when_local_is_down():
    log = Log(); p = build(log, local_up=False, remote_text='```json\n{"summary": "from sarvam"}\n```')
    out = p.chat_json("s", "u", SCHEMA)
    assert out["summary"] == "from sarvam" and len(log.remote) == 1 and p.last_source == "sarvam" and p.model == "sarvam-x"
    assert "JSON" in log.remote[0]["messages"][0]["content"]                     # told to answer with the schema only
    assert p.available()["ok"] is True and p.available()["local"] is False


def test_sarvam_takes_over_when_local_fails_mid_call():
    log = Log()
    p = build(log, local_text="not json")                                         # local answers garbage -> counts as a failure
    assert p.chat_json("s", "u", SCHEMA)["summary"] == "ok" and p.last_source == "sarvam"


def test_both_unavailable_raises_a_clear_error_and_never_crashes_callers():
    log = Log()
    with pytest.raises(OllamaError, match="no AI model available"):
        build(log, local_up=False, remote_status=500).chat_json("s", "u", SCHEMA)
    with pytest.raises(OllamaError):
        build(log, local_up=False, allow_remote=False).chat_json("s", "u", SCHEMA)  # fallback switched off
    with pytest.raises(OllamaError):
        build(log, local_up=False, key=None).chat_json("s", "u", SCHEMA)            # no key configured
    assert build(Log(), local_up=False, allow_remote=False, key=None).available()["ok"] is False


def test_remote_call_cap(monkeypatch):
    monkeypatch.setenv("AI_REMOTE_MAX_CALLS", "2")
    log = Log(); p = build(log, local_up=False)
    p.chat_json("s", "u", SCHEMA); p.chat_json("s", "u", SCHEMA)
    with pytest.raises(OllamaError):
        p.chat_json("s", "u", SCHEMA)
    assert len(log.remote) == 2


def test_move_review_is_completed_by_sarvam_with_the_same_guards(result, fresh):
    a = next(x for x in result["actions"] if x["type"] == "WH_TO_STORE"); ev = a["evidence"]; pol = result["manifest"]["policy"]
    good = json.dumps({"outcome": "CONFIRM", "plain_explanation": "The store will run out, so this makes sense.", "reasoning": "Rules pass.", "evidence_refs": ["validator"],
                       "risks": [], "missing_information": [], "confidence": "medium", "preferred_candidate": None})
    out = review.review_one(build(Log(), local_up=False, remote_text=good), a, ev, [], pol)
    assert out["state"] == "completed" and out["review"]["outcome"] == "CONFIRM"
    bad = good.replace("The store will run out", "987654 units will be lost")
    out = review.review_one(build(Log(), local_up=False, remote_text=bad), a, ev, [], pol)
    assert out["state"] == "failed" and "ungrounded" in out["error"]               # invented numbers rejected for Sarvam too


def test_ai_unavailable_everywhere_leaves_review_pending(result, fresh):
    db.create_run("t1", "manual", "d", "h")
    acts = result["actions"][:3]
    out = review.review_run("t1", acts, {a["id"]: a["evidence"] for a in acts}, {}, result["manifest"]["policy"], client=build(Log(), local_up=False, allow_remote=False), limit=2)
    assert out["ai_status"] == "pending" and db.get_run("t1")["ai_status"] == "pending"


def test_shipment_summaries_fall_back_to_sarvam_then_to_the_template(fresh):
    from nodal import pipeline
    b = pipeline.load_bundle()
    sh = shp.build(b["actions"], b["stores"], b["manifest"]["seasonality"], {})[:2]
    def text_for(s): return json.dumps({"summary": f"{s['city']} gets {s['units']} units by {shp._short(s['latest_arrival'])}."})
    log = Log()
    p = build(log, local_up=False, remote_text=text_for(sh[0]))
    st = shp.summarise([sh[0]], p)
    assert st["sarvam"] == 1 and sh[0]["summary_source"] == "sarvam" and sh[0]["city"] in sh[0]["summary"]
    st2 = shp.summarise([dict(sh[0])], build(Log(), local_up=False, remote_text=text_for(sh[0])))
    assert st2["cached"] == 1                                                      # cached, and labelled as written by Sarvam
    sh1 = dict(sh[1]); st3 = shp.summarise([sh1], build(Log(), local_up=False, remote_status=500))
    assert st3["template"] == 1 and sh1["summary_source"] == "template"            # nothing available: standard wording


def test_plan_answer_uses_sarvam_when_local_is_down(fresh):
    ships = [{"store": "PAT-01", "city": "Patna", "kind": "deliver", "units": 323, "n_moves": 54, "value_mrp": 1000000, "urgency": "High", "latest_arrival": "2026-11-08",
              "summary": "Patna is running low.", "needs_signoff": 5}]
    log = Log(); p = build(log, local_up=False, remote_text='{"answer": "PAT-01 gets 323 units by 2026-11-08."}')
    out = qa.answer("What is going to PAT-01?", "c1", "run1", None, ships, {"shipments": 1, "units": 323, "needs_signoff": 5, "approved": 0, "awaiting": 54}, local_client=p)
    assert out["source"] == "sarvam" and "323" in out["answer"] and len(log.remote) == 1
    off = qa.answer("What is going to PAT-01 today?", "c2", "run1", None, ships, {"shipments": 1, "units": 323, "needs_signoff": 5, "approved": 0, "awaiting": 54},
                    local_client=build(Log(), local_up=False, allow_remote=False))
    assert off["source"] == "plan" and "323 units" in off["answer"]                 # everything off: deterministic answer


def test_prose_answer_from_sarvam_is_wrapped_for_single_text_schemas_but_not_for_complex_ones():
    log = Log(); p = build(log, local_up=False, remote_text="Patna is running low on 54 items.")
    assert p.chat_json("s", "u", SCHEMA)["summary"] == "Patna is running low on 54 items."
    with pytest.raises(OllamaError):                                              # multi-field schema (a move review) cannot be rebuilt from prose
        build(Log(), local_up=False, remote_text="Looks fine.").chat_json("s", "u", {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a", "b"]})


def test_oversized_but_valid_sarvam_review_is_trimmed_not_rejected(result, fresh):
    a = next(x for x in result["actions"] if x["type"] == "WH_TO_STORE"); pol = result["manifest"]["policy"]
    long = json.dumps({"outcome": "CONFIRM", "plain_explanation": "The store will run out, so this makes sense.", "reasoning": "Rules pass. " * 120,
                       "evidence_refs": ["validator", "move_type", "reason", "units", "sku", "from", "to", "lead_days", "arrival_date", "dispatch_date"], "risks": [], "missing_information": [],
                       "confidence": "low", "preferred_candidate": None})
    out = review.review_one(build(Log(), local_up=False, remote_text=long), a, a["evidence"], [], pol)
    assert out["state"] == "completed" and len(out["review"]["reasoning"]) <= 700 and len(out["review"]["evidence_refs"]) == 8
    wrong = json.dumps({**json.loads(long), "evidence_refs": ["made_up_key"]})
    assert review.review_one(build(Log(), local_up=False, remote_text=wrong), a, a["evidence"], [], pol)["state"] == "failed"       # strict checks unchanged
