"""Independent hard-rule validator.

Re-derives the post-plan stock state straight from the source tables + the action list (it does NOT use the allocator's
ledger) and checks every hard rule from the brief. Any action involved in a violation is excluded, never left approvable.
"""
from __future__ import annotations
import math
from collections import defaultdict
import pandas as pd

from .loader import Data

RULES = [
    ("supply", "Every source unit is allocated at most once (outgoing <= on-hand; in-transit never re-dispatched)"),
    ("capacity", "No store ends above stock capacity (receipts only where room exists)"),
    ("min_display", "No category is pushed below its minimum display by a dispatch"),
    ("protected", "S++ protected styles never leave their store"),
    ("donor_cover", "Donor keeps >= 30 days cover after giving (SKU and style level)"),
    ("zero_sales", "Zero-sales stock is only moved when slowness is proven (not a stock-out artefact)"),
    ("lead_time", "Arrival = dispatch + lead time from the lead_times table"),
    ("no_churn", "No store both receives and sends the same SKU in one plan"),
    ("data_integrity", "No action touches a position with contradictory source data"),
    ("approval_flag", "Moves above the approval threshold are flagged for human approval"),
    ("explained", "Every action carries a one-line explanation and evidence"),
]


def validate(d: Data, vel: pd.DataFrame, actions: list, seas: dict) -> dict:
    t, pol = d.t, d.policy
    wh_id = d.warehouse_id
    stores = t["stores"].set_index("store_id")
    items = t["items"].set_index("sku_id")
    snap = t["stock_snapshot"]
    on_hand = {(r.location_id, r.sku_id): int(r.on_hand) for r in snap.itertuples()}
    in_tr = {(r.location_id, r.sku_id): int(r.in_transit) for r in snap.itertuples()}
    cap = stores.stock_capacity_units.to_dict()
    md = {(r.tier, r.category): int(r.min_display_units) for r in t["min_display"].itertuples()}
    prot = {(r.store_id, r.style_id) for r in t["flagship_protected"].itertuples()}
    inbound = defaultdict(int)
    for r in t["warehouse_inbound"].itertuples():
        inbound[(r.sku_id, r.expected_date_dt)] += int(r.qty)

    V = []  # (rule, detail, [action ids])
    out, inn = defaultdict(int), defaultdict(int)
    by_dst, by_src = defaultdict(list), defaultdict(list)
    for a in actions:
        out[(a["source"], a["sku_id"])] += a["qty"]
        inn[(a["destination"], a["sku_id"])] += a["qty"]
        by_dst[a["destination"]].append(a)
        by_src[a["source"]].append(a)

    # 1 supply
    for (loc, sku), q in out.items():
        if loc == wh_id:
            continue
        if q > max(on_hand.get((loc, sku), 0), 0):
            ids = [a["id"] for a in by_src[loc] if a["sku_id"] == sku]
            V.append(("supply", f"{loc}/{sku}: {q} units leave but only {on_hand.get((loc, sku), 0)} on hand (in-transit units cannot be re-dispatched)", ids))
    wh_on = defaultdict(int)
    for a in actions:
        if a["source"] == wh_id and a["supply_basis"] == "on_hand":
            wh_on[a["sku_id"]] += a["qty"]
    for sku, q in wh_on.items():
        if q > on_hand.get((wh_id, sku), 0):
            V.append(("supply", f"warehouse {sku}: {q} dispatched from on-hand but only {on_hand.get((wh_id, sku), 0)} in stock", [a["id"] for a in by_src[wh_id] if a["sku_id"] == sku and a["supply_basis"] == "on_hand"]))
    for sku in {a["sku_id"] for a in actions if a["source"] == wh_id and a["supply_basis"] != "on_hand"}:
        by_date = defaultdict(int)
        for a in actions:
            if a["source"] == wh_id and a["supply_basis"] != "on_hand" and a["sku_id"] == sku:
                by_date[a["dispatch_date"]] += a["qty"]
        for dstr, q in by_date.items():
            # a dispatch day may use every planned inbound arrival up to and including that date
            have = sum(v for (s, dt), v in inbound.items() if s == sku and dt <= pd.Timestamp(dstr))
            if q > have:
                V.append(("supply", f"warehouse {sku}: {q} units scheduled for {dstr} but planned inbound by then is {have}", [a["id"] for a in by_src[wh_id] if a["sku_id"] == sku and a["dispatch_date"] == dstr]))

    # 2 capacity & 3 min display
    cat_of = items.cat2.to_dict()
    load0, load1 = defaultdict(int), defaultdict(int)
    cat0, cat1 = defaultdict(int), defaultdict(int)
    for (loc, sku), oh in on_hand.items():
        if loc == wh_id:
            continue
        n = max(oh, 0) + in_tr.get((loc, sku), 0)
        load0[loc] += n
        cat0[(loc, cat_of[sku])] += n
    for k, v_ in load0.items():
        load1[k] = v_
    for k, v_ in cat0.items():
        cat1[k] = v_
    cat_out = defaultdict(int)
    for a in actions:
        c = cat_of[a["sku_id"]]
        if a["source"] != wh_id:
            load1[a["source"]] -= a["qty"]; cat1[(a["source"], c)] -= a["qty"]; cat_out[(a["source"], c)] += a["qty"]
        if a["destination"] != wh_id:
            load1[a["destination"]] += a["qty"]; cat1[(a["destination"], c)] += a["qty"]
    for st, c in cap.items():
        if load1[st] > c and not (load0[st] > c and load1[st] <= load0[st] and not any(a["destination"] == st for a in actions)):
            V.append(("capacity", f"{st}: final load {load1[st]} > capacity {c}", [a["id"] for a in by_dst[st]]))
    for (st, cat), n in cat1.items():
        mn = md.get((stores.at[st, "tier"], cat), 0)
        if n < mn and cat_out[(st, cat)] > 0:
            V.append(("min_display", f"{st} {cat}: {n} < minimum display {mn} after dispatches", [a["id"] for a in by_src[st] if cat_of[a["sku_id"]] == cat]))

    # 4 protected, 8 data integrity, 6 lead time, 10 approval, 11 explained
    for a in actions:
        if a["source"] != wh_id and (a["source"], a["style_id"]) in prot:
            V.append(("protected", f"{a['source']} is S++ and {a['style_id']} is protected", [a["id"]]))
        for loc in (a["source"], a["destination"]):
            if loc != wh_id and on_hand.get((loc, a["sku_id"]), 0) < 0:
                V.append(("data_integrity", f"{loc}/{a['sku_id']} has negative source stock", [a["id"]]))
        if a["type"] == "WH_TO_STORE":
            lead = d.lead_wh[stores.at[a["destination"], "zone"]]
        elif a["type"] == "STORE_TO_STORE":
            s, dd = stores.loc[a["source"]], stores.loc[a["destination"]]
            lead = d.lead_s2s["same_city"] if s.city == dd.city else (d.lead_s2s["same_zone"] if s.zone == dd.zone else d.lead_s2s["diff_zone"])
        else:
            lead = d.lead_wh[stores.at[a["source"], "zone"]]
        gap = (pd.Timestamp(a["arrival_date"]) - pd.Timestamp(a["dispatch_date"])).days
        if gap != lead or pd.Timestamp(a["dispatch_date"]) <= d.as_of:
            V.append(("lead_time", f"{a['id']}: arrival-dispatch={gap}d vs lead {lead}d", [a["id"]]))
        if (a["value_mrp"] > pol.approval_value_mrp) != bool(a.get("requires_approval")):
            V.append(("approval_flag", f"{a['id']}: value {a['value_mrp']} vs threshold {pol.approval_value_mrp}", [a["id"]]))
        if not a.get("explanation") or not a.get("evidence"):
            V.append(("explained", f"{a['id']} lacks explanation/evidence", [a["id"]]))

    # 5 donor cover (final state), 7 zero-sales trust
    donors = {(a["source"], a["sku_id"]) for a in actions if a["source"] != wh_id}
    dc = pol.donor_min_cover_days
    v_of = lambda st, sku: (vel.at[(st, sku), "vel"] * max(1.0, vel.at[(st, sku), "season"])) if (st, sku) in vel.index else 0.0
    for st, sku in sorted(donors):
        left = on_hand.get((st, sku), 0) - out[(st, sku)]
        v = v_of(st, sku)
        ids = [a["id"] for a in by_src[st] if a["sku_id"] == sku]
        if v > 1e-9 and left / v < dc - 1e-6:
            V.append(("donor_cover", f"{st}/{sku}: {left} left = {left / v:.1f}d cover < {dc}d", ids))
        style = items.at[sku, "style_id"]
        sk = [s for s in items.index[items.style_id == style] if (st, s) in on_hand]
        sl = sum(max(on_hand[(st, s)], 0) - out[(st, s)] for s in sk)
        sv = sum(v_of(st, s) for s in sk)
        if sv > 1e-9 and sl / sv < dc - 1e-6:
            V.append(("donor_cover", f"{st}/{style}: style cover {sl / sv:.1f}d < {dc}d", ids))
        if (st, sku) in vel.index and vel.at[(st, sku), "units"] == 0 and vel.at[(st, sku), "zero_class"] != "proven_slow":
            V.append(("zero_sales", f"{st}/{sku}: zero sales but class={vel.at[(st, sku), 'zero_class']}", ids))

    # 9 churn
    for (st, sku), q in out.items():
        if q > 0 and st != wh_id and inn.get((st, sku), 0) > 0:
            V.append(("no_churn", f"{st} both sends and receives {sku}", [a["id"] for a in actions if a["sku_id"] == sku and st in (a["source"], a["destination"])]))

    counts = defaultdict(int)
    for r, _, ids in V:
        counts[r] += 1
    summary = [{"rule": k, "description": desc, "violations": counts.get(k, 0), "passed": counts.get(k, 0) == 0} for k, desc in RULES]
    return {"violations": [{"rule": r, "detail": dd, "action_ids": ids} for r, dd, ids in V], "rules": summary}
