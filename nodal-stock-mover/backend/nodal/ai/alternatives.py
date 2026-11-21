"""Deterministic alternative-movement candidates. Built only from NODAL's own ledger and forecasts; the LLM can rank them, never create them.

Every candidate is (1) feasible under the planner's donor/capacity/protection logic, (2) costed with the same negative-binomial
expected-revenue model the optimiser uses, and (3) re-validated by the independent validator inside the otherwise-unchanged plan.
"""
from __future__ import annotations
import copy, math
import numpy as np
import pandas as pd
from scipy import stats

from ..validate import validate


def _exp_rev(pl, pos, stock: int, mrp: float, days: int = 30) -> float:
    if stock <= 0 or pos.excluded:
        return 0.0
    mu = max(pl.veff(pos, pl.dispatch_day + 3) * days, 1e-9)
    ks = np.arange(1, int(stock) + 1)
    alpha = pl.cfg.get("alpha", 0.05)
    from .. import forecast as F
    sf = stats.poisson.sf(ks - 1, mu) if alpha <= 1e-4 else stats.nbinom.sf(ks - 1, *F.nb_pmf_params(mu, alpha))
    return float(sf.sum()) * mrp


def _final_stock(pos) -> int:
    return pos.on_hand + pos.in_transit + pos.inn - pos.out


def _cover(pl, pos, stock: int, lead: int):
    v = pl.veff(pos, pl.dispatch_day + lead)
    return None if v <= 1e-9 else round(max(0.0, stock - v * (pl.dispatch_day + lead)) / v, 1)


def _make_alt(a: dict, cid: str, kind: str, src: str, dst: str, qty: int, lead: int, policy_threshold: int) -> dict:
    d0 = pd.Timestamp(a["dispatch_date"])
    typ = "WH_TO_STORE" if src.startswith("WH") else "STORE_TO_STORE"
    value = a["unit_mrp"] * qty
    alt = {**copy.deepcopy({k: v for k, v in a.items() if k not in ("evidence",)}), "type": typ, "type_label": "Warehouse → Store" if typ == "WH_TO_STORE" else "Store → Store",
           "source": src, "destination": dst, "qty": qty, "lead_days": lead, "arrival_date": str((d0 + pd.Timedelta(days=lead)).date()), "value_mrp": value,
           "requires_approval": value > policy_threshold, "supply_basis": "on_hand", "explanation": f"Alternative {cid} ({kind}): {qty}× {a['sku_id']} {src}→{dst}", "evidence": {"checks": ["re-validated by the independent validator"]},
           "id": a["id"], "flags": [f for f in a.get("flags", []) if f != "approval_required"] + (["approval_required"] if value > policy_threshold else [])}
    return alt


def generate(ctx: dict, actions: list[dict], targets: list[dict], per_move: int = 2) -> dict:
    """ctx = engine result['_ctx']. Returns {move_id: [candidate, ...]} for the target moves; candidates are validated."""
    d, vel, seas, pl = ctx["d"], ctx["vel"], ctx["seas"], ctx["pl"]
    thr = d.policy.approval_value_mrp
    wh = d.warehouse_id
    out = {}
    for a in targets:
        sku, q, src, dst = a["sku_id"], a["qty"], a["source"], a["destination"]
        if a["type"] == "PULL_BACK" or (dst, sku) not in pl.P:
            continue
        mrp = float(a["unit_mrp"])
        P = pl.P
        dpos = P[(dst, sku)]
        raw = []
        orig_src = P.get((src, sku)) if src != wh else None
        # --- alternative donors (same destination, same quantity)
        for st in pl.by_sku.get(sku, []):
            if st in (src, dst) or st == wh:
                continue
            ap = P[(st, sku)]
            if pl.donor_surplus(ap) >= q:
                lead = pl.lead_s2s(st, dst)
                kind = "donor_instead_of_warehouse" if src == wh else "alternative_donor"
                s_a, s_d = _final_stock(ap), _final_stock(dpos)
                if src == wh:
                    delta = -(_exp_rev(pl, ap, s_a, mrp) - _exp_rev(pl, ap, s_a - q, mrp))          # donor loses sales; warehouse keeps the units
                else:
                    o = orig_src
                    delta = (_exp_rev(pl, o, _final_stock(o) + q, mrp) - _exp_rev(pl, o, _final_stock(o), mrp)) - (_exp_rev(pl, ap, s_a, mrp) - _exp_rev(pl, ap, s_a - q, mrp))
                raw.append((kind, st, dst, lead, delta, {"source_cover_after_days": _cover(pl, ap, s_a - q, 0), "dest_cover_after_days": _cover(pl, dpos, s_d, lead),
                                                         "note": ("warehouse keeps these units" if src == wh else f"{src} keeps its stock instead") + ("; clears ageing stock" if pl.is_ageing(ap) else "")}))
        # --- warehouse instead of an inter-store transfer
        if src != wh and pl.wh.get(sku, 0) >= q:
            o = orig_src
            lead = pl.lead_wh(dst)
            delta = _exp_rev(pl, o, _final_stock(o) + q, mrp) - _exp_rev(pl, o, _final_stock(o), mrp)
            raw.append(("warehouse_instead_of_store", wh, dst, lead, delta, {"source_cover_after_days": _cover(pl, o, _final_stock(o) + q, 0), "dest_cover_after_days": _cover(pl, dpos, _final_stock(dpos), lead),
                                                                          "note": f"uses {q} of the warehouse's remaining {pl.wh.get(sku, 0)} units"}))
        # --- alternative destination for ageing / transfer stock
        if a["type"] == "STORE_TO_STORE" and a["reason"] in ("ageing_relocation", "over_capacity", "surplus_rebalance"):
            for st, pos, day, n, r in pl.recipients(sku, src, "S2S"):
                if st == dst or n < q or r < q:
                    continue
                lead = pl.lead_s2s(src, st)
                d_final, n_final = _final_stock(dpos), _final_stock(pos)
                delta = (_exp_rev(pl, pos, n_final + q, mrp) - _exp_rev(pl, pos, n_final, mrp)) - (_exp_rev(pl, dpos, d_final, mrp) - _exp_rev(pl, dpos, d_final - q, mrp))
                raw.append(("alternative_destination", src, st, lead, delta, {"source_cover_after_days": None if orig_src is None else _cover(pl, orig_src, _final_stock(orig_src), 0),
                                                                              "dest_cover_after_days": _cover(pl, pos, n_final + q, lead), "note": f"{st} still has unmet demand"}))
        raw.sort(key=lambda r: (-r[4], r[3]))
        cands = []
        for kind, s, t, lead, delta, extra in raw[: per_move + 3]:
            if len(cands) >= per_move:
                break
            cid = f"ALT-{len(cands) + 1}"
            alt = _make_alt(a, cid, kind, s, t, q, lead, thr)
            acts2 = [alt if x["id"] == a["id"] else x for x in actions]
            res = validate(d, vel, acts2, seas)
            if res["violations"]:
                continue                                                                # independent validator rejects it
            cands.append({"id": cid, "kind": kind, "source": s, "destination": t, "qty": q, "lead_days": lead, "action": alt,
                          "metrics": {"delta_expected_revenue": round(delta), "horizon_days": 30, "basis": "negative-binomial expected sales from the ML forecast, revenue at MRP", **extra},
                          "validation": {"passed": True, "rules_checked": len(res["rules"])}})
        if cands:
            out[a["id"]] = cands
    return out
