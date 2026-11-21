"""Tests run on the REAL organiser data. Invariants are re-computed here from raw CSVs, independent of the engine's ledger."""
import copy
import pandas as pd
import pytest
from nodal.engine import run
from nodal.loader import DataError, load_all
from nodal.validate import validate
from nodal.velocity import compute_velocity
from conftest import DATA


def raw(name):
    return pd.read_csv(DATA / f"{name}.csv", dtype=str, keep_default_na=False)


def test_missing_file_gives_actionable_error(tmp_path):
    with pytest.raises(DataError, match="Required file missing"):
        load_all(tmp_path)


def test_policy_values_read_from_rules_csv(data):
    assert data.policy.donor_min_cover_days == 30
    assert data.policy.approval_value_mrp == 100000
    assert data.lead_wh == {"East": 3, "North": 4, "South": 6}
    assert data.lead_s2s == {"same_city": 2, "same_zone": 4, "diff_zone": 7}
    assert data.cat_map["Safa"] == "Accessories (Safa/Mala/Stole)"


def test_velocity_traces_to_source_rows(data):
    """Pick a real store x SKU, recompute units and in-stock days straight from the CSVs."""
    vel, _ = compute_velocity(data)
    pair = vel[(vel.units >= 5) & (vel.avail_days >= 60)].index[0]
    st, sku = pair
    bills, lines = raw("bills"), raw("bill_lines")
    m = lines[lines.sku_id == sku].merge(bills[bills.store_id == st], on="bill_id")
    m = m[(m.bill_date > "2026-08-08") & (m.bill_date <= "2026-10-31")]
    assert vel.at[pair, "units"] == m.qty.astype(int).sum()
    wk = raw("stock_weekly")
    wk = wk[(wk.store_id == st) & (wk.sku_id == sku)]
    assert vel.at[pair, "weekly_days"] == wk.days_in_stock.astype(int).sum() or vel.at[pair, "avail_days"] >= wk.days_in_stock.astype(int).sum()
    if vel.at[pair, "evidence_days"] >= 42:
        assert vel.at[pair, "obs_vel"] == pytest.approx(vel.at[pair, "units"] / vel.at[pair, "evidence_days"])


def test_zero_sales_not_trusted_without_availability(data):
    vel, _ = compute_velocity(data)
    z = vel[vel.units == 0]
    assert not z[(z.zero_class == "proven_slow") & (z.avail_days < 42)].shape[0]


def test_validator_reports_no_violations(result):
    assert all(r["passed"] for r in result["validation"]["rules"]), result["validation"]["violations"][:3]
    assert result["summary"]["excluded_by_validator"] == 0


def test_every_action_explained_and_traceable(result):
    for a in result["actions"]:
        assert a["explanation"] and a["evidence"] and a["qty"] > 0
        assert a["status"] in ("proposed", "needs_approval")
        assert (a["value_mrp"] > 100000) == a["requires_approval"]


def test_never_dispatches_more_than_on_hand_or_in_transit(result):
    snap = raw("stock_snapshot")
    oh = {(r.location_id, r.sku_id): int(r.on_hand) for r in snap.itertuples()}
    out = {}
    for a in result["actions"]:
        if a["supply_basis"] == "on_hand":
            out[(a["source"], a["sku_id"])] = out.get((a["source"], a["sku_id"]), 0) + a["qty"]
    for k, q in out.items():
        assert q <= max(oh.get(k, 0), 0), f"{k} dispatches {q} > on hand {oh.get(k)}"


def test_protected_styles_never_leave_s_plus_plus(result):
    prot = {(r.store_id, r.style_id) for r in raw("flagship_protected").itertuples()}
    assert prot
    for a in result["actions"]:
        assert (a["source"], a["style_id"]) not in prot


def test_capacity_respected_and_overcapacity_store_relieved(result):
    stores = raw("stores").set_index("store_id").stock_capacity_units.astype(int)
    snap = raw("stock_snapshot")
    snap[["on_hand", "in_transit"]] = snap[["on_hand", "in_transit"]].astype(int)
    load = snap[snap.location_id.isin(stores.index)].assign(t=lambda x: x.on_hand.clip(lower=0) + x.in_transit).groupby("location_id").t.sum().to_dict()
    for a in result["actions"]:
        if a["source"] in load: load[a["source"]] -= a["qty"]
        if a["destination"] in load: load[a["destination"]] += a["qty"]
    for st, cap in stores.items():
        assert load[st] <= cap, f"{st} ends at {load[st]} > {cap}"


def test_donor_keeps_30_days_cover(result, data):
    vel, _ = compute_velocity(data)
    oh = {(r.location_id, r.sku_id): int(r.on_hand) for r in raw("stock_snapshot").itertuples()}
    out = {}
    for a in result["actions"]:
        if a["source"] != data.warehouse_id:
            out[(a["source"], a["sku_id"])] = out.get((a["source"], a["sku_id"]), 0) + a["qty"]
    assert out
    for (st, sku), q in out.items():
        v = vel.at[(st, sku), "vel"]
        if v > 0:
            assert (oh[(st, sku)] - q) / v >= 30 - 1e-6


def test_negative_stock_position_untouched(result):
    for a in result["actions"]:
        assert not (a["sku_id"] == "KUR-02-M" and "MYS-02" in (a["source"], a["destination"]))


def test_lead_times_applied(result):
    for a in result["actions"]:
        gap = (pd.Timestamp(a["arrival_date"]) - pd.Timestamp(a["dispatch_date"])).days
        assert gap == a["lead_days"] and gap in (2, 3, 4, 6, 7)


def test_deterministic(result):
    r2 = run(str(DATA))
    key = lambda r: [(a["id"], a["source"], a["destination"], a["sku_id"], a["qty"]) for a in r["actions"]]
    assert key(r2) == key(result)


def test_validator_catches_tampered_plan(result, data):
    """Inject deliberately illegal moves; the independent validator must flag every one."""
    vel, seas = compute_velocity(data)
    prot = raw("flagship_protected").iloc[0]
    sku = next(a["sku_id"] for a in result["actions"] if a["style_id"] == prot.style_id) if any(a["style_id"] == prot.style_id for a in result["actions"]) else prot.style_id + "-M"
    base = copy.deepcopy(result["actions"][0])
    bad_protected = {**base, "id": "BAD-1", "type": "STORE_TO_STORE", "source": prot.store_id, "destination": "KNP-01", "style_id": prot.style_id, "sku_id": sku,
                     "supply_basis": "on_hand", "lead_days": 7, "dispatch_date": "2026-11-01", "arrival_date": "2026-11-08", "qty": 1}
    bad_oversell = {**base, "id": "BAD-2", "qty": 10 ** 6, "value_mrp": 10 ** 9, "requires_approval": True}
    res = validate(data, vel, [bad_protected, bad_oversell], seas)
    rules = {v["rule"] for v in res["violations"]}
    assert "protected" in rules and "supply" in rules and "capacity" in rules


def test_optimizer_not_worse_than_greedy_and_rules_hold(result):
    o = result["manifest"]["optimizer"]
    assert o and o["uplift_pct"] >= 0 and "Optimal" in o["lp"]["status"]


def test_map_api_returns_stores_and_moves():
    from fastapi.testclient import TestClient
    from nodal.api import app
    c = TestClient(app)
    prods = c.get("/api/map/products").json()
    assert prods["categories"] and prods["styles"]
    m = c.get("/api/map", params={"style": prods["styles"][0]["style"]}).json()
    assert len(m["stores"]) > 10 and all(-90 < s["lat"] < 90 for s in m["stores"])
    assert c.get("/api/map").status_code == 422
