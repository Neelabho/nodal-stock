"""Daily automation: idempotency, missing files, snapshots, recovery, scheduling. Runs the real pipeline once (≈1 min)."""
import datetime as dt, gzip, json, shutil
from pathlib import Path
import pytest
from nodal import db, pipeline
from conftest import DATA


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import os
    t = tmp_path_factory.mktemp("pipe")
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "NODAL_PUBLISHED", "NODAL_SNAPSHOTS", "NODAL_INBOX", "NODAL_NOTIFY_FILE")}
    os.environ.update(DATABASE_URL=f"sqlite:///{t}/p.db", NODAL_PUBLISHED=str(t / "pub"), NODAL_SNAPSHOTS=str(t / "snap"), NODAL_INBOX=str(t / "inbox"), NODAL_NOTIFY_FILE=str(t / "notify.log"))
    db.reset_engine()
    yield t
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    db.reset_engine()


def test_full_run_publishes_snapshot_bundle_and_records_stages(env):
    out = pipeline.daily(with_ai=False, dataset=DATA)
    assert out["status"] == "completed", out
    run = db.get_run(out["run_id"])
    assert set(run["stages"]) >= {"ingest_validate", "snapshot", "forecast_allocate_validate", "alternative_candidates", "publish"}
    assert all(s["status"] == "completed" for s in run["stages"].values())
    snap = env / "snap" / out["run_id"]
    assert (snap / "manifest.json").exists() and (snap / "stores.csv").exists()
    b = pipeline.load_bundle()
    assert b["run_id"] == out["run_id"] and len(b["actions"]) == run["metrics"]["moves"] and all(r["passed"] for r in b["validation"]["rules"])
    assert (env / "pub" / f"run_{out['run_id']}.json.gz").exists()                # history is kept, never overwritten
    assert len(db.moves_of(out["run_id"])) == len(b["actions"])
    assert "completed" in (env / "notify.log").read_text()


def test_same_dataset_is_not_processed_or_published_twice(env):
    first = db.list_runs()[0]
    out = pipeline.daily(with_ai=False, dataset=DATA)
    assert out["status"] == "skipped_duplicate" and out["run_id"] == first["run_id"]
    assert len([r for r in db.list_runs() if r["input_hash"] == first["input_hash"]]) == 1


def test_missing_dataset_stops_the_run_with_a_reason(env, tmp_path):
    d = tmp_path / "partial"; d.mkdir()
    shutil.copy(DATA / "stores.csv", d / "stores.csv")
    out = pipeline.daily(with_ai=False, dataset=d)
    assert out["status"] == "failed" and "missing required datasets" in out["error"]
    assert db.get_run(out["run_id"])["status"] == "failed"


def test_interrupted_run_is_closed_not_silently_resumed(env):
    db.create_run("stuck-1", "daily", None, "zz")
    import sqlalchemy as sa
    with db.engine().begin() as c:
        c.execute(sa.update(db.runs).where(db.runs.c.run_id == "stuck-1").values(created_at="2020-01-01T00:00:00+00:00"))
    pipeline.mark_stale_runs()
    r = db.get_run("stuck-1")
    assert r["status"] == "failed" and "interrupted" in r["error"]


def test_watched_folder_picks_the_newest_complete_dataset(env, tmp_path, monkeypatch):
    inbox = tmp_path / "inbox2"; (inbox / "day1").mkdir(parents=True)
    monkeypatch.setenv("NODAL_INBOX", str(inbox))
    assert pipeline.resolve_dataset()[1] == "configured_dir"                         # incomplete inbox -> fallback
    for f in DATA.glob("*.csv"):
        shutil.copy(f, inbox / "day1" / f.name)
    d, label = pipeline.resolve_dataset()
    assert label == "inbox" and d.name == "day1" and pipeline.input_hash(d) == pipeline.input_hash(DATA)


def test_schedule_uses_configured_timezone():
    from zoneinfo import ZoneInfo
    now = dt.datetime(2026, 11, 21, 5, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert pipeline.seconds_until("06:00", "Asia/Kolkata", now) == 3600
    assert pipeline.seconds_until("04:00", "Asia/Kolkata", now) == 23 * 3600
