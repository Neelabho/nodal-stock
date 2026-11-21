"""Add data: validation of uploads, overlay on the supplied dataset, local-only access, single-flight recalculation."""
import importlib, time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from nodal import db, pipeline
from conftest import DATA

BUNDLE = Path(__file__).resolve().parents[1] / "published" / "latest.json.gz"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/d.db"); monkeypatch.setenv("NODAL_INBOX", str(tmp_path / "inbox")); monkeypatch.setenv("NODAL_OVERLAYS", str(tmp_path / "ov"))
    monkeypatch.setenv("NODAL_BUNDLE", str(BUNDLE)); monkeypatch.setenv("NODAL_DATA_DIR", str(DATA)); monkeypatch.delenv("NODAL_MODE", raising=False)
    db.reset_engine()
    import nodal.api as m
    importlib.reload(m)
    yield m, TestClient(m.app, client=("127.0.0.1", 5000)), tmp_path
    db.reset_engine()


def test_validation_rejects_bad_files():
    ok = (DATA / "stores.csv").read_bytes()
    assert pipeline.validate_upload("stores", ok) == []
    assert "missing required column" in pipeline.validate_upload("stores", b"store_id,city\nA,B\n")[0]
    assert pipeline.validate_upload("stores", b"")[0] == "file is empty"
    assert "no data rows" in pipeline.validate_upload("stores", (DATA / "stores.csv").read_text().splitlines()[0].encode() + b"\n")[0]
    assert "not one of the datasets" in pipeline.validate_upload("payroll", ok)[0]
    assert "UTF-8" in pipeline.validate_upload("stores", b"\xff\xfe\x00bad")[0]


def test_partial_upload_overlays_on_supplied_data(env):
    m, c, tmp = env
    assert pipeline.resolve_dataset()[1] == "configured_dir"
    body = (DATA / "stores.csv").read_text().replace("Lucknow", "Lucknow").encode() + b"\n"
    assert c.put("/api/inbox/stores", content=body).status_code == 200
    d, label = pipeline.resolve_dataset()
    assert label.startswith("uploaded:stores") and (d / "stores.csv").read_bytes() == (tmp / "inbox" / "stores.csv").read_bytes()
    assert (d / "bills.csv").read_bytes() == (DATA / "bills.csv").read_bytes()                    # untouched files come from the supplied data
    assert pipeline.input_hash(d) != pipeline.input_hash(DATA) or body.strip() == (DATA / "stores.csv").read_bytes().strip()
    files = {f["name"]: f["state"] for f in c.get("/api/inbox").json()["files"]}
    assert files["stores"] == "uploaded" and files["bills"] == "supplied"
    assert c.delete("/api/inbox/stores").status_code == 200 and pipeline.resolve_dataset()[1] == "configured_dir"


def test_bad_upload_is_rejected_with_reasons_and_not_saved(env):
    m, c, tmp = env
    r = c.put("/api/inbox/stores", content=b"store_id,city\nA,B\n")
    assert r.status_code == 422 and "missing required column" in r.json()["detail"]["problems"][0]
    assert not (tmp / "inbox" / "stores.csv").exists()


def test_uploads_are_local_only_and_disabled_in_public_mode(env, monkeypatch):
    m, c, tmp = env
    remote = TestClient(m.app, client=("203.0.113.9", 5000))
    assert remote.put("/api/inbox/stores", content=(DATA / "stores.csv").read_bytes()).status_code == 403
    monkeypatch.setattr(m, "MODE", "published")
    assert c.get("/api/inbox").status_code == 403 and c.post("/api/pipeline/run").status_code == 403
    assert c.get("/api/capabilities").json()["can_upload"] is False


def test_recalculation_is_single_flight_and_reports_result(env, monkeypatch):
    m, c, tmp = env
    def slow(force=False, with_ai=True, **kw):
        time.sleep(0.4)
        return {"run_id": "x", "status": "skipped_duplicate", "message": "same data"}
    monkeypatch.setattr(pipeline, "daily", slow)
    assert c.post("/api/pipeline/run").json()["started"] is True
    assert c.post("/api/pipeline/run").status_code == 409                                          # a second click cannot start another run
    for _ in range(30):
        s = c.get("/api/pipeline/status").json()
        if not s["running"]:
            break
        time.sleep(0.1)
    assert s["result"]["status"] == "skipped_duplicate"
