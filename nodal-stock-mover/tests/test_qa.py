"""Ask feature: routing (privacy), caching, rate limiting, failure handling. Sarvam is mocked; the real key is never used in tests."""
import importlib, json, re, subprocess
from pathlib import Path
import httpx, pytest
from fastapi.testclient import TestClient
from nodal import db, qa

BUNDLE = Path(__file__).resolve().parents[1] / "published" / "latest.json.gz"


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/q.db"); db.reset_engine()
    yield
    db.reset_engine()


def sarvam(calls, status=200, text="Stock cover is how many days current stock will last."):
    def h(r):
        calls.append(json.loads(r.content))
        if status != 200:
            return httpx.Response(status, json={"error": "x"}, headers={"retry-after": "5"})
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    return qa.SarvamClient(api_key="test-key", model="m", transport=httpx.MockTransport(h))


SHIPS = [{"store": "PAT-01", "city": "Patna", "kind": "deliver", "units": 323, "n_moves": 54, "value_mrp": 1000000, "urgency": "High", "latest_arrival": "2026-11-08", "summary": "Patna is running low.", "needs_signoff": 5}]
STATUS = {"shipments": 22, "units": 2845, "needs_signoff": 24, "approved": 0, "awaiting": 600}


def test_router_keeps_plan_questions_off_sarvam():
    assert qa.route("What is stock cover?") == "general"
    assert qa.route("Why do big moves need sign-off?") == "general"
    for q in ("What is going to PAT-01?", "How many shipments are waiting today?", "How many units go to KUR-04-M?", "which store is short"):
        assert qa.route(q) == "plan", q


def test_general_question_is_sent_with_public_facts_only_and_then_cached(fresh):
    calls = []
    c = sarvam(calls)
    r1 = qa.answer("What is stock cover?", "c1", "run1", {"donor_min_cover_days": 30, "approval_value_mrp": 100000}, SHIPS, STATUS, sarvam=c)
    assert r1["source"] == "sarvam" and len(calls) == 1
    sent = json.dumps(calls[0])
    assert "PAT-01" not in sent and "2845" not in sent and "323" not in sent        # no plan data reaches the third party
    assert "30 days" in sent and "100,000" in sent                                   # only public rules and definitions
    r2 = qa.answer("  what is STOCK cover?? ", "c2", "run1", None, SHIPS, STATUS, sarvam=c)   # same question, different wording/client
    assert r2["cached"] is True and r2["source"] == "cache" and len(calls) == 1


def test_plan_question_never_calls_sarvam_and_uses_shipment_facts(fresh):
    calls = []
    out = qa.answer("What is going to PAT-01?", "c1", "run1", None, SHIPS, STATUS, sarvam=sarvam(calls))
    assert calls == [] and out["source"] == "plan" and "323 units" in out["answer"] and "Patna" in out["answer"]
    out2 = qa.answer("How many shipments are waiting today?", "c1", "run1", None, SHIPS, STATUS, sarvam=sarvam(calls))
    assert calls == [] and "22 shipments" in out2["answer"]


def test_per_minute_rate_limit(fresh, monkeypatch):
    monkeypatch.setenv("QA_PER_MINUTE", "3")
    calls = []; c = sarvam(calls)
    for i in range(3):
        qa.answer(f"What is concept number {i}?", "c1", "r", None, SHIPS, STATUS, sarvam=c)
    with pytest.raises(qa.RateLimited):
        qa.answer("What is another concept?", "c1", "r", None, SHIPS, STATUS, sarvam=c)
    qa.answer("What is another concept?", "c2", "r", None, SHIPS, STATUS, sarvam=c)       # a different client is unaffected


def test_daily_limits_apply_to_uncached_calls_only(fresh, monkeypatch):
    monkeypatch.setenv("QA_PER_DAY", "2"); monkeypatch.setenv("QA_PER_MINUTE", "50")
    calls = []; c = sarvam(calls)
    qa.answer("Explain concept A", "c1", "r", None, SHIPS, STATUS, sarvam=c); qa.answer("Explain concept B", "c1", "r", None, SHIPS, STATUS, sarvam=c)
    with pytest.raises(qa.RateLimited):
        qa.answer("Explain concept C", "c1", "r", None, SHIPS, STATUS, sarvam=c)
    assert qa.answer("Explain concept A", "c1", "r", None, SHIPS, STATUS, sarvam=c)["cached"] is True   # cached answers stay free
    monkeypatch.setenv("QA_PER_DAY", "50"); monkeypatch.setenv("QA_GLOBAL_PER_DAY", "2")
    with pytest.raises(qa.RateLimited):
        qa.answer("Explain concept D", "c9", "r", None, SHIPS, STATUS, sarvam=c)


def test_sarvam_busy_or_down_is_reported_not_crashing(fresh):
    with pytest.raises(qa.RateLimited):
        qa.answer("Explain concept E", "c1", "r", None, SHIPS, STATUS, sarvam=sarvam([], status=429))
    with pytest.raises(RuntimeError):
        qa.answer("Explain concept F", "c1", "r", None, SHIPS, STATUS, sarvam=sarvam([], status=500))
    with pytest.raises(RuntimeError):
        qa.answer("Explain concept G", "c1", "r", None, SHIPS, STATUS, sarvam=qa.SarvamClient(api_key=None))


def test_input_validation(fresh):
    for bad in ("", "hi", "x" * 301):
        with pytest.raises(ValueError):
            qa.answer(bad, "c1", "r", None, SHIPS, STATUS, sarvam=sarvam([]))


def test_no_secrets_in_tracked_files_and_env_is_ignored():
    """Generic scan: any key-shaped string (sk_ + 20+ chars) in a tracked text file fails the test. No real key appears in this file."""
    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"\bsk_[A-Za-z0-9_]{20,}\b")
    files = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    for f in files:
        p = root / f
        if p.suffix in (".py", ".js", ".md", ".yaml", ".yml", ".json", ".example", ".html", ".css", ".sh", ".template") and p.exists() and p.stat().st_size < 2_000_000:
            assert not pattern.search(p.read_text(errors="ignore")), f"secret-shaped string in {f}"
    assert subprocess.run(["git", "check-ignore", "-q", "nodal-stock-mover/.env"], cwd=root).returncode == 0


@pytest.mark.skipif(not BUNDLE.exists(), reason="no published bundle")
def test_ask_endpoint_rate_limits_with_429(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/a.db"); monkeypatch.setenv("NODAL_BUNDLE", str(BUNDLE)); monkeypatch.setenv("QA_PER_MINUTE", "2")
    db.reset_engine()
    import nodal.api as m
    importlib.reload(m)
    c = TestClient(m.app)
    for _ in range(2):
        assert c.post("/api/ask", json={"question": "How many shipments are waiting today?"}, headers={"X-Client-Id": "z"}).status_code == 200
    r = c.post("/api/ask", json={"question": "How many shipments are waiting today?"}, headers={"X-Client-Id": "z"})
    assert r.status_code == 429 and r.headers["retry-after"]
    assert c.post("/api/ask", json={"question": "ab"}, headers={"X-Client-Id": "y"}).status_code == 422
    db.reset_engine()
