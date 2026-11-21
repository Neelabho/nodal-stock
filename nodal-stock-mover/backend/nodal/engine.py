"""Orchestrator: load -> audit -> velocity -> allocate -> finalise -> validate -> summary + manifest."""
from __future__ import annotations
import math, time, datetime as dt
from collections import Counter, defaultdict
import pandas as pd

from .loader import load_all
from .audit import run_audit
from .velocity import compute_velocity
from .allocate import plan
from .validate import validate
from .policy import ENGINE_VERSION, SOURCES

TYPE_LABEL = {"WH_TO_STORE": "Warehouse → Store", "STORE_TO_STORE": "Store → Store", "PULL_BACK": "Pull back"}
REASON_LABEL = {
    "under_stocked": "Under-stocked", "display_minimum": "Minimum display", "scheduled_inbound": "Scheduled inbound",
    "ageing_relocation": "Ageing → where it sells", "surplus_rebalance": "Surplus rebalance", "over_capacity": "Over capacity",
    "ageing_pullback": "Ageing pull-back",
}


def _fmt(x, nd=0):
    """Cover in days; very long cover is shown as '1y+' because 45,000 days is meaningless to a person."""
    if x is None or x >= 365:
        return "1y+"
    return f"{x:.{nd}f}d"


def explain(a: dict, seas: dict, stores: pd.DataFrame, H: int) -> str:
    e, r = a["evidence"], a["reason"]
    q, sku, src, dst = a["qty"], a["sku_id"], a["source"], a["destination"]
    head = f"{q}× {sku}"
    rc = e.get("recipient", {})
    dn = e.get("donor", {})
    fest = ""
    if rc and rc.get("forecast_ml") is not None:
        st = stores.at[dst, "state"] if dst in stores.index else None
        fx = seas[st] if st else {}
        tail = f"; {fx['festival']} in {fx['offset_days']}d" if fx.get("festival") and rc["velocity_used"] > 1.05 * max(rc["velocity_trailing"], 1e-9) else ""
        fest = f" (ML forecast; trailing {rc['velocity_trailing']:.2f}/day{tail})"
    elif rc and rc.get("season_index", 1) > 1.05:
        st = stores.at[dst, "state"] if dst in stores.index else None
        if st and seas[st]["festival"]:
            fest = f" (incl. ×{rc['season_index']:.2f} {seas[st]['festival']} uplift)"
    if a["type"] == "WH_TO_STORE" and r == "display_minimum":
        d_ = e["display"]
        return f"{head} {src}→{dst}: {dst} holds {d_['units_before_plan']} {d_['category']} units vs minimum display {d_['minimum']}; {sku} chosen as lowest-cover size here; arrives {a['arrival_date']}."
    if a["type"] == "WH_TO_STORE":
        sup = e["supply"]
        short = " Warehouse short, so allocated to equalise cover across stores." if sup.get("shortfall") else ""
        sched = f" Scheduled: depends on planned inbound ({a['dispatch_date']})." if r == "scheduled_inbound" else ""
        return (f"{head} {src}→{dst}, arrives {a['arrival_date']} ({a['lead_days']}d): sells {rc['velocity_used']:.2f}/day{fest} → cover at arrival "
                f"{_fmt(rc['cover_before_days'])}, {_fmt(rc['cover_after_days'])} after; warehouse {sup['warehouse_available_before']}→{sup['warehouse_available_after']}.{short}{sched}")
    if a["type"] == "STORE_TO_STORE":
        keep = f"{src} keeps {_fmt(dn['sku_cover_after_days'])} cover (≥30d)"
        if r == "ageing_relocation":
            return (f"{head} {src}→{dst}, arrives {a['arrival_date']} ({a['lead_days']}d): ageing stock (oldest unit {int(dn['oldest_unit_age_days'])}d, "
                    f"{'no sales in ' + str(dn['in_stock_days_84d']) + ' in-stock days' if dn['units_84d'] == 0 else 'cover ' + _fmt(dn['sku_cover_before_days']) + 'd'}) moves to {dst} where it sells {rc['velocity_used']:.2f}/day{fest}; {keep}.")
        if r == "over_capacity":
            return f"{head} {src}→{dst}, arrives {a['arrival_date']}: {src} is over capacity; {dst} sells {rc['velocity_used']:.2f}/day{fest} with {_fmt(rc['cover_before_days'])} cover; {keep}."
        if r == "display_minimum":
            d_ = e["display"]
            return f"{head} {src}→{dst}: {dst} {d_['category']} below minimum display ({d_['units_before_plan']} vs {d_['minimum']}) and warehouse has none; {keep}; {src} stays above its own display minimum."
        return (f"{head} {src}→{dst}, arrives {a['arrival_date']} ({a['lead_days']}d): {dst} sells {rc['velocity_used']:.2f}/day{fest}, cover {_fmt(rc['cover_before_days'])}→{_fmt(rc['cover_after_days'])}; "
                f"{src} has surplus ({_fmt(dn['sku_cover_before_days'])} cover) and {keep}.")
    # pull back
    if r == "over_capacity":
        c = e["capacity"]
        return (f"{head} {src}→{dst} (pull-back): {src} holds {c['capacity'] + c['excess_at_start']} units vs capacity {c['capacity']}; this SKU "
                f"{'has had no sales in ' + str(dn['in_stock_days_84d']) + ' in-stock days' if dn['units_84d'] == 0 else 'is the slowest-moving surplus'}; no store needs it now.")
    return (f"{head} {src}→{dst} (pull-back): oldest unit {int(dn['oldest_unit_age_days'])}d, "
            f"{'no sales in ' + str(dn['in_stock_days_84d']) + ' in-stock days' if dn['units_84d'] == 0 else 'cover ' + _fmt(dn['on_hand_before'] / max(dn['velocity_effective'], 1e-9)) + 'd'}; no store currently needs it. Consolidate at {dst} (assumed destination).")


def _tier(a):
    r = a["reason"]
    cb = a["evidence"].get("recipient", {}).get("cover_before_days")
    if r in ("display_minimum", "over_capacity"):
        return 1
    if r in ("ageing_relocation", "ageing_pullback", "surplus_rebalance") and a["type"] != "STORE_TO_STORE":
        return 4
    if r == "ageing_relocation":
        return 4
    if a["type"] != "PULL_BACK" and (cb is not None and cb <= 0.05 or (cb is None and False)):
        return 1
    if a["type"] != "PULL_BACK" and cb is not None and cb < 7:
        return 2
    if a["type"] == "PULL_BACK":
        return 4
    return 3


TIER_NAME = {1: "Critical", 2: "High", 3: "Normal", 4: "Housekeeping"}


def finalise(pl, d, seas):
    stores = d.t["stores"].set_index("store_id")
    pol = d.policy
    acts = pl.actions
    for a in acts:
        a["requires_approval"] = bool(a["value_mrp"] > pol.approval_value_mrp)
        a["priority_tier"] = _tier(a)
        a["urgency"] = TIER_NAME[a["priority_tier"]]
        ev = a["evidence"]
        side = ev.get("recipient") if a["type"] != "PULL_BACK" else ev.get("donor")
        a["confidence"] = (side or {}).get("confidence", "medium")
        cb = ev.get("recipient", {}).get("cover_before_days")
        a["cover_before_days"] = cb
        a["cover_after_days"] = ev.get("recipient", {}).get("cover_after_days")
        flags = []
        if a["requires_approval"]:
            flags.append("approval_required")
        if a["supply_basis"] != "on_hand":
            flags.append("scheduled_inbound")
        if a["confidence"] == "low":
            flags.append("low_confidence")
        if side and side.get("shrunk_to_peer"):
            flags.append("peer_estimate")
        if a["type"] == "PULL_BACK":
            flags.append("assumed_destination")
        if a["type"] != "PULL_BACK" and a["destination"] in stores.index:
            st = stores.at[a["destination"], "state"]
            fd = seas[st].get("festival_date")
            if fd and seas[st]["index"] > 1.05 and pd.Timestamp(a["arrival_date"]) > pd.Timestamp(fd):
                flags.append(f"arrives_after_{seas[st]['festival']}")
        a["flags"] = flags
    acts.sort(key=lambda a: (a["priority_tier"], a["dispatch_date"], a["cover_before_days"] if a["cover_before_days"] is not None else 1e9, -a["value_mrp"], a["source"], a["destination"], a["sku_id"]))
    for i, a in enumerate(acts, 1):
        a["id"] = f"NDL-{i:04d}"
    for a in acts:
        a["explanation"] = explain(a, seas, stores, pol.seasonality_horizon_days)
        a["type_label"] = TYPE_LABEL[a["type"]]
        a["reason_label"] = REASON_LABEL[a["reason"]]
        a["status"] = "needs_approval" if a["requires_approval"] else "proposed"
    return acts


def run(data_dir: str) -> dict:
    t0 = time.time()
    d = load_all(data_dir)
    audit = run_audit(d)
    vel, seas = compute_velocity(d)
    pl = plan(d, vel, seas)
    opt_cmp = None
    if d.policy.use_optimizer:
        import dataclasses
        d_greedy = dataclasses.replace(d, policy=dataclasses.replace(d.policy, use_optimizer=False))
        pg = plan(d_greedy, vel, seas)
        rev_lp, un_lp = pl.expected_revenue(); rev_g, un_g = pg.expected_revenue()
        opt_cmp = {"method": "LP (HiGHS) maximising expected revenue captured, vs greedy cover-equalising", "expected_revenue_lp": round(rev_lp), "expected_revenue_greedy": round(rev_g),
                   "expected_units_lp": round(un_lp), "expected_units_greedy": round(un_g), "uplift_pct": round(100 * (rev_lp / max(rev_g, 1) - 1), 2), "lp": pl.lp_stats,
                   "horizon_days": 30, "note": "expected demand ~ negative binomial from the ML forecast; revenue at MRP; same hard rules for both"}
    acts = finalise(pl, d, seas)
    excluded, rounds = [], 0
    while rounds < 6:
        rounds += 1
        res = validate(d, vel, acts, seas)
        bad = {i for v in res["violations"] for i in v["action_ids"]}
        if not bad:
            break
        for a in acts:
            if a["id"] in bad:
                a["status"] = "excluded"
                excluded.append({"id": a["id"], "reason": next(v["detail"] for v in res["violations"] if a["id"] in v["action_ids"]), "rule": next(v["rule"] for v in res["violations"] if a["id"] in v["action_ids"])})
        acts = [a for a in acts if a["id"] not in bad]
    final = validate(d, vel, acts, seas)
    stores = d.t["stores"].set_index("store_id")
    pol = d.policy

    block = Counter(k[0] for k in pl.blocked)
    block_samples = defaultdict(list)
    for (rule, st, sku), detail in sorted(pl.blocked.items()):
        if len(block_samples[rule]) < 8:
            block_samples[rule].append({"store": st, "sku": sku, "detail": detail})
    unresolved = sorted(pl.unresolved, key=lambda u: (u["kind"] != "over_capacity_unresolved", u["kind"] != "display_minimum_unmet", -(u.get("units") or 0)))

    def summarise(sel):
        return {"actions": len(sel), "units": sum(a["qty"] for a in sel), "value_mrp": sum(a["value_mrp"] for a in sel)}
    by_type = {k: summarise([a for a in acts if a["type"] == k]) for k in TYPE_LABEL}
    by_reason = {k: summarise([a for a in acts if a["reason"] == k]) for k in REASON_LABEL}
    wh_out = Counter()
    for a in acts:
        if a["source"] == d.warehouse_id and a["supply_basis"] == "on_hand":
            wh_out[a["sku_id"]] += a["qty"]
    summary = {
        "total": summarise(acts), "by_type": by_type, "by_reason": by_reason,
        "needs_approval": sum(1 for a in acts if a["requires_approval"]),
        "scheduled": sum(1 for a in acts if a["supply_basis"] != "on_hand"),
        "low_confidence": sum(1 for a in acts if a["confidence"] == "low"),
        "urgency": dict(Counter(a["urgency"] for a in acts)),
        "stores_touched": len({a["destination"] for a in acts if a["destination"] in stores.index} | {a["source"] for a in acts if a["source"] in stores.index}),
        "warehouse_units_dispatched_now": int(sum(wh_out.values())), "warehouse_units_on_hand": int(sum(pl.wh_total0.values())) if hasattr(pl, "wh_total0") else None,
        "unresolved_counts": dict(Counter(u["kind"] for u in unresolved)), "unresolved_units": int(sum(u.get("units", 0) for u in unresolved if u["kind"] == "unmet_demand")),
        "blocked_counts": dict(block), "excluded_by_validator": len(excluded),
    }
    hl = []
    for f in audit["findings"]:
        if f["check"] == "store above stock capacity" and f["count"]:
            hl.append({"tone": "bad", "title": "Over-capacity store found and corrected", "text": f["detail"] + ". NODAL blocks all receipts there and pulls the excess back (to a store that sells it, else the warehouse).", "tab": "register", "filter": {"reason": "over_capacity"}})
        if f["check"] == "category below minimum display" and f["count"]:
            hl.append({"tone": "warn", "title": "Minimum-display breaches found", "text": f["detail"] + ". Filled first, ahead of ordinary replenishment.", "tab": "register", "filter": {"reason": "display_minimum"}})
        if f["check"] == "negative on_hand" and f["count"]:
            hl.append({"tone": "warn", "title": "Contradictory stock excluded", "text": f["detail"] + ". Never used as a source or destination.", "tab": "quality"})
    sd = [(s, x) for s, x in seas.items() if x.get("festival")]
    if sd:
        hl.append({"tone": "info", "title": "Festival demand baked into velocity", "text": "; ".join(f"{s}: {x['festival']} {x['festival_date']} ×{x['index']:.2f}" for s, x in sd) + " (measured from last year's same-offset sales window, not assumed).", "tab": "method"})
    um = [u for u in unresolved if u["kind"] == "unmet_demand"]
    if um:
        hl.append({"tone": "warn", "title": "Demand the network cannot cover", "text": f"{len(um)} store×SKU positions (≈{sum(u['units'] for u in um)} units) still need stock after warehouse supply and eligible donors are exhausted. Listed explicitly, not hidden.", "tab": "safety"})
    ag = by_reason["ageing_relocation"]; apb = by_reason["ageing_pullback"]
    hl.append({"tone": "info", "title": "Ageing stock", "text": f"{ag['units']} units of ageing stock relocated to stores that sell them; {apb['units']} units pulled back where nobody sells them.", "tab": "register", "filter": {"reason": "ageing_relocation"}})
    zc = vel.zero_class.value_counts().to_dict()
    manifest = {
        "engine_version": ENGINE_VERSION, "run_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "runtime_seconds": round(time.time() - t0, 2),
        "as_of": str(d.as_of.date()), "dispatch_date": str((d.as_of + pd.Timedelta(days=pol.dispatch_offset_days)).date()),
        "source_dir": "manyavar-vch3-data/csv", "source_hash": d.source_hash, "file_hashes": d.manifest_files, "policy": pol.as_dict(), "policy_sources": SOURCES, "policy_notes": d.policy_notes,
        "lead_times": {"warehouse_to_store_by_zone": d.lead_wh, "store_to_store": d.lead_s2s}, "seasonality": seas,
        "velocity": {"positions": int(len(vel)), "zero_sales_classification": zc, "confidence": vel.confidence.value_counts().to_dict(),
                     "method": ("Poisson gradient-boosted forecast of units in the next 28 days per store x SKU (lags, peer aggregates, price, tier, festival/wedding calendar, last-year window); floored by the in-stock trailing rate x festival index where a position was out of stock" if pol.use_ml_forecast else "units sold in last 84 days / in-stock days; thin evidence shrunk toward same-SKU peer rate; x festival index"),
                     "ml_forecast": bool(pol.use_ml_forecast), "floor_applied": int(vel.floor_applied.sum())},
        "validator_rounds": rounds, "optimizer": opt_cmp,
    }
    summary["highlights"] = hl
    ledger_in, ledger_out = defaultdict(int), defaultdict(int)
    for a in acts:
        ledger_in[(a["destination"], a["sku_id"])] += a["qty"]; ledger_out[(a["source"], a["sku_id"])] += a["qty"]
    items = d.t["items"].set_index("sku_id")
    positions = [{"store": p.store, "sku": p.sku, "style": items.at[p.sku, "style_id"], "style_name": items.at[p.sku, "style_name"], "category": items.at[p.sku, "category"], "size": items.at[p.sku, "size"],
                  "on_hand": p.on_hand, "in_transit": p.in_transit, "daily": float(p.vel_ml if p.vel_ml is not None and not pd.isna(p.vel_ml) else p.vel_eff), "units84": p.units,
                  "plan_in": ledger_in.get((p.store, p.sku), 0), "plan_out": ledger_out.get((p.store, p.sku), 0)} for p in pl.P.values()]
    wh = [{"sku": k, "on_hand": int(v), "style": items.at[k, "style_id"], "category": items.at[k, "category"], "plan_out": ledger_out.get((d.warehouse_id, k), 0)} for k, v in pl.wh_total0.items() if k in items.index]
    return {"_ctx": {"d": d, "vel": vel, "seas": seas, "pl": pl, "acts": acts}, "positions": positions, "warehouse": wh, "manifest": manifest, "audit": audit, "summary": summary, "actions": acts, "validation": final, "excluded": excluded,
            "blocked_samples": dict(block_samples), "unresolved": unresolved[:400],
            "stores": [{"store_id": i, "city": r.city, "tier": r.tier, "zone": r.zone, "capacity": int(r.stock_capacity_units)} for i, r in stores.iterrows()]}
