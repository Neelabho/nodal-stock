"""Shortages: grouping, business actions (local AI, Sarvam fallback, template), grounding, caching, API."""
import importlib, json
from pathlib import Path
import httpx, pytest
from fastapi.testclient import TestClient
from nodal import db, shortages as sht
from test_provider import build as provider, Log

BUNDLE = Path(__file__).resolve().parents[1] / "published" / "latest.json.gz"


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/s.db"); db.reset_engine()
    yield
    db.reset_engine()


def _groups(result, inbound=None):
    return sht.build(result["unresolved"], result["stores"], inbound or {}, result["manifest"]["seasonality"])


def test_every_shortage_is_in_exactly_one_store_group_with_structured_reasons(result):
    um = [u for u in result["unresolved"] if u["kind"] == "unmet_demand"]
    assert um and all(u["reasons"] and u["value_mrp"] > 0 and u["category"] and u["size"] for u in um)
    g = _groups(result)
    assert sum(x["n_items"] for x in g) == len(um) and sum(x["units_short"] for x in g) == sum(u["units"] for u in um)
    assert sum(x["value_mrp"] for x in g) == sum(u["value_mrp"] for u in um)
    assert len({x["store"] for x in g}) == len(g) and [x["value_mrp"] for x in g] == sorted((x["value_mrp"] for x in g), reverse=True)


def test_planned_warehouse_deliveries_are_attached_and_only_future_ones_count():
    rows = [{"expected_date": "2026-11-01", "sku_id": "A", "qty": 5}, {"expected_date": "2026-11-10", "sku_id": "A", "qty": 7}, {"expected_date": "2026-11-16", "sku_id": "A", "qty": 3}, {"expected_date": "2026-11-16", "sku_id": "B", "qty": 9}]
    m = sht.inbound_map(rows, "2026-11-01")
    assert m["A"] == {"qty": 10, "first": "2026-11-10", "first_qty": 7} and m["B"]["first"] == "2026-11-16"      # the delivery on the plan date is not 'coming'


def test_template_action_follows_the_cause():
    base = {"city": "Patna", "store": "PAT-01", "n_items": 10, "with_planned_delivery": 0, "first_delivery": None, "festival": "Diwali", "festival_in_days": 8, "causes": {}}
    assert "full" in sht.template_action({**base, "causes": {"store_full": 6}}) and "Diwali is 8 days away" in sht.template_action(base)
    assert "bring it forward" in sht.template_action({**base, "with_planned_delivery": 6, "first_delivery": "2026-11-16"}) and "2026-11-16" in sht.template_action({**base, "with_planned_delivery": 6, "first_delivery": "2026-11-16"})
    assert "replenishment order" in sht.template_action(base)


def test_action_source_is_local_then_sarvam_then_template_and_is_cached(result, fresh):
    g = _groups(result)[:1]
    good = lambda x: json.dumps({"action": f"{x['city']} is short {x['units_short']} units, so place a replenishment order."})
    st = sht.summarise([dict(g[0])], provider(Log(), local_text=good(g[0])))
    assert st["local_ai"] == 1
    g1 = [dict(g[0])]; log = Log()
    st = sht.summarise(g1, provider(log, local_up=False, remote_text=good(g[0]).replace("replenishment order", "replenishment order for the listed items")))
    assert st["sarvam"] == 1 and g1[0]["action_source"] == "sarvam" and len(log.remote) == 1
    g2 = [dict(g[0])]; log2 = Log()
    assert sht.summarise(g2, provider(log2, local_up=False, remote_text="{}"))["cached"] == 1 and g2[0]["action_source"] == "sarvam" and log2.remote == []   # cached, no second call
    g3 = [dict(g[0], units_short=g[0]["units_short"] + 1)]
    assert sht.summarise(g3, provider(Log(), local_up=False, remote_status=500))["template"] == 1 and g3[0]["action_source"] == "template"          # nothing available
    g4 = [dict(g[0], units_short=g[0]["units_short"] + 2)]
    bad = json.dumps({"action": f"{g[0]['city']} will lose 987654 units, so order now."})
    assert sht.summarise(g4, provider(Log(), local_up=False, remote_text=bad))["template"] == 1                                                       # invented number rejected


@pytest.mark.skipif(not BUNDLE.exists(), reason="no published bundle")
def test_shortage_api_lists_groups_and_details(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/a.db"); monkeypatch.setenv("NODAL_BUNDLE", str(BUNDLE)); db.reset_engine()
    import nodal.api as m
    importlib.reload(m)
    c = TestClient(m.app)
    j = c.get("/api/shortages").json()
    assert j["totals"]["items"] > 0 and j["totals"]["value_mrp"] > 0 and all(g["action"] and g["action_source"] for g in j["groups"])
    d = c.get(f"/api/shortages/{j['groups'][0]['id']}").json()
    assert len(d["items"]) == d["n_items"] and d["items"][0]["reasons"]
    assert c.get("/api/shortages/NOPE").status_code == 404
    db.reset_engine()
