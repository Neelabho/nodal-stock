"""Deterministic allocation engine.

One ledger, every unit allocated at most once. Order of passes:
  A  ageing store stock  -> stores that actually sell the SKU
  B  category minimum-display fills (warehouse first, then eligible donors)
  C  warehouse on-hand   -> under-stocked positions (water-filling on cover)
  D  surplus store->store for needs the warehouse could not meet
  E  pull-backs: over-capacity stores (to a store that sells it, else warehouse) and ageing stock nobody sells
  F  warehouse planned inbound -> remaining needs, as dated scheduled dispatches
The independent validator (validate.py) re-checks the finished plan and excludes anything that breaks a hard rule.
"""
from __future__ import annotations
import heapq, math
from collections import defaultdict
from dataclasses import dataclass
import numpy as np
import pandas as pd

from .loader import Data
from . import forecast as _F
from scipy import stats as _stats
from scipy.optimize import linprog
import scipy.sparse as _sp

EPS = 1e-9


@dataclass
class Pos:
    store: str
    sku: str
    on_hand: int
    in_transit: int
    it_day: int            # days from as_of when in-transit arrives
    age_days: float | None
    units: int
    evidence_days: int
    avail_days: int
    elig_days: int
    vel: float
    vel_eff: float
    season: float
    conf: str
    zero_class: str
    shrunk: bool
    peer_vel: float
    peer_src: str
    obs_vel: float
    protected: bool = False
    vel_base: float = 0.0
    vel_ml: float | None = None
    floor: bool = False
    fc_lo: float | None = None
    fc_hi: float | None = None
    excluded: str = ""
    out: int = 0
    inn: int = 0


def _ceil(x):
    return int(math.ceil(x - EPS))


class Planner:
    def __init__(self, d: Data, vel: pd.DataFrame, seas: dict):
        self.d, self.p, self.seas = d, d.policy, seas
        t = d.t
        self.as_of = d.as_of
        self.H = self.p.seasonality_horizon_days
        self.stores = t["stores"].set_index("store_id")
        self.items = t["items"].set_index("sku_id")
        self.prod_sku = set(t["items"].loc[t["items"].is_packaging == 0, "sku_id"])
        self.md = {(r.tier, r.category): int(r.min_display_units) for r in t["min_display"].itertuples()}
        self.prot = {(r.store_id, r.style_id) for r in t["flagship_protected"].itertuples()}
        self.cap = self.stores.stock_capacity_units.to_dict()
        self.actions: list[dict] = []
        self.blocked: dict = {}
        self.unresolved: list[dict] = []
        self.P: dict[tuple, Pos] = {}
        self.by_sku: dict[str, list] = defaultdict(list)       # sku -> [store ids with a position]
        self.by_store: dict[str, list] = defaultdict(list)     # store -> [sku ids]
        self.style_skus_at: dict[tuple, list] = defaultdict(list)  # (store, style) -> [sku]
        snap = t["stock_snapshot"]
        s = snap[snap.location_id != d.warehouse_id].set_index(["location_id", "sku_id"])
        self.wh = snap[snap.location_id == d.warehouse_id].set_index("sku_id").on_hand.clip(lower=0).to_dict()
        self.wh_total0 = dict(self.wh)
        self.cfg = _F.load_config()
        self.lp_gain = {}
        self.lp_stats = None
        self.wh_inbound = defaultdict(list)
        for r in t["warehouse_inbound"].itertuples():
            self.wh_inbound[r.expected_date_dt].append((r.sku_id, int(r.qty), r.status))
        self.peer_tier = vel.dropna(subset=["peer_tier"]).reset_index()[["sku_id", "tier", "peer_tier"]].drop_duplicates(["sku_id", "tier"]).set_index(["sku_id", "tier"]).peer_tier.to_dict()
        self.peer_all = vel.dropna(subset=["peer_all"]).reset_index()[["sku_id", "peer_all"]].drop_duplicates("sku_id").set_index("sku_id").peer_all.to_dict()
        for (st, sku), r in vel.iterrows():
            if sku not in self.prod_sku:
                continue
            row = s.loc[(st, sku)] if (st, sku) in s.index else None
            oh = int(row.on_hand) if row is not None else 0
            it = int(row.in_transit) if row is not None else 0
            itd = row.in_transit_arrives_on_dt if row is not None else pd.NaT
            old = row.oldest_stock_received_on_dt if row is not None else pd.NaT
            pos = Pos(st, sku, max(oh, 0), it, int((itd - self.as_of).days) if pd.notna(itd) else 0,
                      float((self.as_of - old).days) if pd.notna(old) and oh > 0 else None,
                      int(r.units), int(r.evidence_days), int(r.avail_days), int(r.elig_days), float(r.vel), float(r.vel_eff), float(r.season),
                      r.confidence, r.zero_class, bool(r.shrunk), float(r.peer_vel), r.peer_src, float(r.obs_vel),
                      vel_base=float(r.vel_base), vel_ml=None if pd.isna(r.vel_ml) else float(r.vel_ml), floor=bool(r.floor_applied), fc_lo=None if pd.isna(r.get('fc_lo', np.nan)) else float(r.fc_lo), fc_hi=None if pd.isna(r.get('fc_hi', np.nan)) else float(r.fc_hi))
            if oh < 0:
                pos.excluded = f"negative on_hand ({oh}) in source data - contradictory, position excluded"
            pos.protected = (st, self.items.at[sku, "style_id"]) in self.prot
            self._register(pos)
        self.store_load = defaultdict(int)
        self.cat_total = defaultdict(int)
        for pos in self.P.values():
            self.store_load[pos.store] += pos.on_hand + pos.in_transit
            self.cat_total[(pos.store, self.items.at[pos.sku, "cat2"])] += pos.on_hand + pos.in_transit
        self.dispatch_day = d.policy.dispatch_offset_days

    # ---------------------------------------------------------------- helpers
    def _register(self, pos: Pos):
        self.P[(pos.store, pos.sku)] = pos
        self.by_sku[pos.sku].append(pos.store)
        self.by_store[pos.store].append(pos.sku)
        self.style_skus_at[(pos.store, self.items.at[pos.sku, "style_id"])].append(pos.sku)

    def _virtual_pos(self, store, sku) -> Pos:
        tier = self.stores.at[store, "tier"]
        pv = self.peer_tier.get((sku, tier), self.peer_all.get(sku, 0.0))
        S = 1.0 if self.p.use_ml_forecast else self.seas[self.stores.at[store, "state"]]["index"]
        pos = Pos(store, sku, 0, 0, 0, None, 0, 0, 0, 0, pv, pv * S, S, "low", "no_evidence", True, pv, "same SKU peers (position not stocked here)", 0.0)
        pos.protected = (store, self.items.at[sku, "style_id"]) in self.prot
        self._register(pos)
        return pos

    def lead_wh(self, store):
        return self.d.lead_wh[self.stores.at[store, "zone"]]

    def lead_s2s(self, a, b):
        A, B = self.stores.loc[a], self.stores.loc[b]
        if A.city == B.city:
            return self.d.lead_s2s["same_city"]
        return self.d.lead_s2s["same_zone"] if A.zone == B.zone else self.d.lead_s2s["diff_zone"]

    def veff(self, pos, arrival_day):
        """Festival index applies only to arrivals inside the uplift window (last-year evidence covers that window only)."""
        return pos.vel * (pos.season if arrival_day <= self.H else 1.0)

    def stock_at(self, pos, day):
        arrive = pos.in_transit if pos.it_day <= day else 0
        return pos.on_hand + arrive + pos.inn - pos.out

    def proj(self, pos, day):
        return max(0.0, self.stock_at(pos, day) - self.veff(pos, day) * day)

    def cover(self, pos, day, extra=0):
        v = self.veff(pos, day)
        return (self.proj(pos, day) + extra) / v if v > EPS else math.inf

    def room(self, store):
        return self.cap[store] - self.store_load[store]

    def need(self, pos, day):
        if pos.excluded or pos.units == 0 or pos.out > 0:   # a store that gives a SKU away in this plan never also receives it
            return 0
        v = self.veff(pos, day)
        if v <= EPS:
            return 0
        pj = self.proj(pos, day)
        if pj / v >= self.p.reorder_trigger_cover_days:
            return 0
        return max(0, _ceil(v * self.p.target_cover_days - pj))

    def donor_surplus(self, pos, record_block=False, want_for=None):
        """Units this position may give away without breaking: 30-day cover (SKU and style), display floor, protection."""
        def blk(rule, detail):
            if record_block:
                self.blocked.setdefault((rule, pos.store, pos.sku), detail)
            return 0
        if pos.excluded:
            return blk("data_contradiction", pos.excluded)
        avail = pos.on_hand - pos.out
        if avail <= 0:
            return 0
        if pos.inn > 0:
            return blk("no_churn", f"{pos.store}/{pos.sku} already receives stock in this plan; it is not also a donor")
        if pos.protected:
            return blk("protected_style", f"{pos.sku} is a top-20 protected style at S++ store {pos.store}")
        if pos.units == 0 and pos.zero_class in ("censored", "no_evidence"):
            return blk("zero_sales_not_trusted", f"zero sales but only {pos.avail_days}/{pos.elig_days} days in stock - not proven slow")
        dc = self.p.donor_min_cover_days
        v = self.veff(pos, self.dispatch_day + 2)
        s = int(math.floor(avail - dc * v + EPS)) if v > EPS else avail
        if s <= 0:
            return blk("donor_cover_30d", f"{pos.store}/{pos.sku}: {avail} on hand at {v:.3f}/day = {avail / v if v > EPS else math.inf:.0f}d cover, no units above the {dc}-day floor")
        style = self.items.at[pos.sku, "style_id"]
        sk = self.style_skus_at[(pos.store, style)]
        st_av = sum(self.P[(pos.store, k)].on_hand - self.P[(pos.store, k)].out for k in sk)
        st_v = sum(self.veff(self.P[(pos.store, k)], self.dispatch_day + 2) for k in sk)
        s2 = int(math.floor(st_av - dc * st_v + EPS)) if st_v > EPS else st_av
        if s2 < s:
            if s2 <= 0:
                return blk("donor_cover_30d_style", f"{pos.store}/{style}: style-level cover would drop below {dc}d")
            s = s2
        cat = self.items.at[pos.sku, "cat2"]
        floor_ = self.md.get((self.stores.at[pos.store, "tier"], cat), 0)
        s3 = self.cat_total[(pos.store, cat)] - floor_
        if s3 < s:
            if s3 <= 0:
                return blk("min_display", f"{pos.store} {cat}: at/below minimum display {floor_}")
            s = s3
        return max(0, s)

    def is_ageing(self, pos):
        if pos.on_hand - pos.out <= 0 or pos.age_days is None or pos.excluded:
            return False
        if pos.age_days < self.p.ageing_min_age_days:
            return False
        if pos.zero_class == "proven_slow":
            return True
        v = self.veff(pos, self.dispatch_day + 2)
        return pos.units > 0 and v > EPS and (pos.on_hand / v) >= self.p.ageing_min_cover_days

    def _cap_room_for_receive(self, store):
        return max(0, self.room(store))

    # ---------------------------------------------------------------- commit
    def commit(self, src, dst, sku, qty, kind, reason, dispatch_day, evidence, supply_basis="on_hand"):
        it = self.items.loc[sku]
        if src != self.d.warehouse_id:
            ps = self.P[(src, sku)]
            ps.out += qty
            self.store_load[src] -= qty
            self.cat_total[(src, it.cat2)] -= qty
        if dst != self.d.warehouse_id:
            pd_ = self.P.get((dst, sku)) or self._virtual_pos(dst, sku)
            pd_.inn += qty
            self.store_load[dst] += qty
            self.cat_total[(dst, it.cat2)] += qty
        if kind == "WH_TO_STORE":
            lead = self.lead_wh(dst)
        elif kind == "STORE_TO_STORE":
            lead = self.lead_s2s(src, dst)
        else:
            lead = self.lead_wh(src)  # a pull-back travels the same corridor as a dispatch (assumption)
        disp = self.as_of + pd.Timedelta(days=dispatch_day)
        arr = disp + pd.Timedelta(days=lead)
        self.actions.append({
            "type": kind, "reason": reason, "sku_id": sku, "style_id": it.style_id, "style_name": it.style_name, "category": it.category,
            "size": it["size"], "is_core_style": int(it.is_core_style), "source": src, "destination": dst, "qty": int(qty),
            "dispatch_date": str(disp.date()), "arrival_date": str(arr.date()), "lead_days": int(lead), "unit_mrp": int(it.mrp),
            "value_mrp": int(it.mrp) * int(qty), "supply_basis": supply_basis, "evidence": evidence,
        })

    def _pos_snapshot(self, pos, day):
        return {"on_hand": pos.on_hand - pos.out, "in_transit": pos.in_transit, "planned_in": pos.inn, "units_84d": pos.units, "in_stock_days_84d": pos.avail_days,
                "window_days": pos.elig_days, "velocity_observed": round(pos.obs_vel, 4), "velocity_used": round(pos.vel, 4), "season_index": round(pos.season, 3),
                "velocity_effective": round(self.veff(pos, day), 4), "velocity_trailing": round(pos.vel_base, 4), "forecast_ml": None if pos.vel_ml is None else round(pos.vel_ml, 4), "floor_applied": pos.floor, "forecast_28d_p90": None if pos.fc_lo is None else [pos.fc_lo, pos.fc_hi], "confidence": pos.conf, "zero_class": pos.zero_class, "shrunk_to_peer": pos.shrunk,
                "peer_velocity": round(pos.peer_vel, 4), "peer_source": pos.peer_src, "oldest_unit_age_days": pos.age_days}

    # ---------------------------------------------------------------- recipient-side selection
    def recipients(self, sku, src_store=None, kind="WH"):
        out = []
        for st in self.by_sku.get(sku, []):
            if st == src_store:
                continue
            pos = self.P[(st, sku)]
            day = self.dispatch_day + (self.lead_wh(st) if kind == "WH" else self.lead_s2s(src_store, st))
            n = self.need(pos, day)
            r = self.room(st)
            if n > 0 and r > 0:
                out.append((st, pos, day, n, r))
        return out

    # ---------------------------------------------------------------- Pass A / D: store -> store
    def move_to_demand(self, donor: Pos, reason, qty_limit=None, kind_tag=""):
        sku, moved = donor.sku, 0
        surplus = self.donor_surplus(donor, record_block=True)
        if qty_limit is not None:
            surplus = min(surplus, qty_limit)
        if surplus <= 0:
            return 0
        cands = []
        for st, pos, day, n, r in self.recipients(sku, donor.store, "S2S"):
            cands.append((self.lead_s2s(donor.store, st), self.cover(pos, day), st))
        for lead, cv, st in sorted(cands):
            if surplus <= 0:
                break
            pos = self.P[(st, sku)]
            day = self.dispatch_day + lead
            n, r = self.need(pos, day), self.room(st)
            q = min(n, r, surplus)
            if q <= 0:
                continue
            ev = self._evidence_transfer(donor, pos, q, day)
            self.commit(donor.store, st, sku, q, "STORE_TO_STORE", reason, self.dispatch_day, ev)
            surplus -= q
            moved += q
        return moved

    def _evidence_transfer(self, donor, rcv, q, day):
        dv = self.veff(donor, self.dispatch_day + 2)
        st_k = self.style_skus_at[(donor.store, self.items.at[donor.sku, "style_id"])]
        st_av = sum(self.P[(donor.store, k)].on_hand - self.P[(donor.store, k)].out for k in st_k)
        st_v = sum(self.veff(self.P[(donor.store, k)], self.dispatch_day + 2) for k in st_k)
        cat = self.items.at[donor.sku, "cat2"]
        fl = self.md.get((self.stores.at[donor.store, "tier"], cat), 0)
        av = donor.on_hand - donor.out
        return {
            "recipient": {**self._pos_snapshot(rcv, day), "cover_before_days": _r(self.cover(rcv, day)), "cover_after_days": _r(self.cover(rcv, day, q)),
                          "projected_stock_at_arrival": _r(self.proj(rcv, day)), "capacity_room_before": self.room(rcv.store), "capacity": self.cap[rcv.store]},
            "donor": {**self._pos_snapshot(donor, self.dispatch_day + 2), "on_hand_before": av, "on_hand_after": av - q,
                      "sku_cover_before_days": _r(av / dv if dv > EPS else math.inf), "sku_cover_after_days": _r((av - q) / dv if dv > EPS else math.inf),
                      "style_cover_after_days": _r((st_av - q) / st_v if st_v > EPS else math.inf),
                      "category_display_after": self.cat_total[(donor.store, cat)] - q, "category_display_min": fl, "protected_style": donor.protected,
                      "is_ageing": self.is_ageing(donor)},
            "checks": ["donor keeps >= %d days cover (SKU & style)" % self.p.donor_min_cover_days, "donor keeps category display minimum", "protected styles excluded",
                       "in-transit units never donated", "receiver within capacity", "arrival = dispatch + lead time"],
        }

    # ---------------------------------------------------------------- LP optimiser
    def expected_revenue(self, days=30):
        """Expected revenue at MRP over the next `days` days given the final planned stock: sum_k P(D >= k) for k <= stock (NegBin demand)."""
        alpha = self.cfg.get("alpha", 0.05); tot = 0.0; units = 0.0
        for pos in self.P.values():
            if pos.excluded or pos.units == 0:
                continue
            S = pos.on_hand + pos.in_transit + pos.inn - pos.out
            if S <= 0:
                continue
            mu = max(self.veff(pos, self.dispatch_day + 3) * days, 1e-9)
            ks = np.arange(1, S + 1)
            sf = _stats.poisson.sf(ks - 1, mu) if alpha <= 1e-4 else _stats.nbinom.sf(ks - 1, *_F.nb_pmf_params(mu, alpha))
            tot += float(sf.sum()) * float(self.items.at[pos.sku, "mrp"]); units += float(sf.sum())
        return tot, units

    def _gain_curve(self, pos, day, K, price):
        """Expected revenue of the k-th received unit = price x P(demand over the target window >= stock_at_arrival + k), demand ~ NegBin."""
        mu = max(self.veff(pos, day) * self.p.target_cover_days, 1e-9)
        s0 = int(math.ceil(self.proj(pos, day) - EPS))
        alpha = self.cfg.get("alpha", 0.05)
        ks = np.arange(1, K + 1) + s0
        if alpha <= 1e-4:
            sf = _stats.poisson.sf(ks - 1, mu)
        else:
            r, p = _F.nb_pmf_params(mu, alpha); sf = _stats.nbinom.sf(ks - 1, r, p)
        return price * sf

    def _lp_solve(self, supply, dispatch_day):
        """Global LP over all SKUs: maximise expected revenue captured subject to warehouse stock per SKU and store capacity room.
        The constraint matrix is a bipartite transportation structure (totally unimodular), so the optimum is integral."""
        cand = []
        for sku in sorted(supply):
            if supply[sku] <= 0:
                continue
            price = float(self.items.at[sku, "mrp"])
            for st in self.by_sku.get(sku, []):
                pos = self.P[(st, sku)]
                day = dispatch_day + self.lead_wh(st)
                n, r = self.need(pos, day), self.room(st)
                K = min(n, r, supply[sku])
                if K > 0 and not pos.excluded:
                    cand.append((st, sku, pos, day, K, self._gain_curve(pos, day, K, price)))
        if not cand:
            return {}
        sku_ix = {s: i for i, s in enumerate(sorted({c[1] for c in cand}))}
        st_ix = {s: i for i, s in enumerate(sorted({c[0] for c in cand}))}
        cols_sku, cols_st, gains, owner = [], [], [], []
        for ci, (st, sku, pos, day, K, g) in enumerate(cand):
            for k in range(K):
                cols_sku.append(sku_ix[sku]); cols_st.append(st_ix[st]); gains.append(g[k]); owner.append(ci)
        nv = len(gains)
        A = _sp.vstack([_sp.csr_matrix((np.ones(nv), (cols_sku, np.arange(nv))), shape=(len(sku_ix), nv)),
                        _sp.csr_matrix((np.ones(nv), (cols_st, np.arange(nv))), shape=(len(st_ix), nv))]).tocsr()
        b = np.array([supply[s] for s in sorted(sku_ix, key=sku_ix.get)] + [max(self.room(s), 0) for s in sorted(st_ix, key=st_ix.get)], float)
        res = linprog(-np.array(gains), A_ub=A, b_ub=b, bounds=(0, 1), method="highs")
        if res.status != 0:
            return None                                   # fall back to the greedy allocator
        y = np.round(res.x).astype(int)
        out = defaultdict(dict); exp_gain = defaultdict(float)
        for j, v in enumerate(y):
            if v:
                st, sku = cand[owner[j]][0], cand[owner[j]][1]
                out[sku][st] = out[sku].get(st, 0) + 1
                exp_gain[(st, sku)] += gains[j]
        self.lp_gain.update(exp_gain)
        self.lp_stats = {"variables": nv, "status": res.message, "objective_expected_revenue": float(-res.fun)}
        return out

    # ---------------------------------------------------------------- warehouse allocation (LP or greedy water-filling)
    def wh_pass(self, supply: dict, dispatch_day: int, basis: str, reason="under_stocked", sched_note=None):
        order = []
        for sku in sorted(supply):
            if supply[sku] <= 0:
                continue
            cs = []
            for st in self.by_sku.get(sku, []):
                pos = self.P[(st, sku)]
                day = dispatch_day + self.lead_wh(st)
                if self.need(pos, day) > 0:
                    cs.append(self.cover(pos, day))
            if cs:
                order.append((min(cs), sku))
        lp = self._lp_solve(supply, dispatch_day) if self.p.use_optimizer else None
        for _, sku in sorted(order):
            sup = supply[sku]
            heap, got, meta = [], defaultdict(int), {}
            if lp is not None:
                for st, q in lp.get(sku, {}).items():
                    pos = self.P[(st, sku)]; day = dispatch_day + self.lead_wh(st)
                    meta[st] = (pos, day, max(self.need(pos, day), q), max(self.room(st), q))
                    got[st] = q
                sup -= sum(got.values())
            else:
                for st in self.by_sku[sku]:
                    pos = self.P[(st, sku)]
                    day = dispatch_day + self.lead_wh(st)
                    n, r = self.need(pos, day), self.room(st)
                    if n > 0 and r > 0 and not pos.excluded:
                        meta[st] = (pos, day, n, r)
                        heapq.heappush(heap, (self.cover(pos, day), -self.veff(pos, day), st))
                while heap and sup > 0:
                    cv, nv, st = heapq.heappop(heap)
                    pos, day, n, r = meta[st]
                    if got[st] >= min(n, r):
                        continue
                    got[st] += 1
                    sup -= 1
                    if got[st] < min(n, r):
                        heapq.heappush(heap, (self.cover(pos, day, got[st]), nv, st))
            wh_before = supply[sku]
            tot = sum(got.values())
            for st in sorted(got):
                pos, day, n, r = meta[st]
                q = got[st]
                ev = {"recipient": {**self._pos_snapshot(pos, day), "cover_before_days": _r(self.cover(pos, day)), "cover_after_days": _r(self.cover(pos, day, q)),
                                    "projected_stock_at_arrival": _r(self.proj(pos, day)), "need_units": n, "target_cover_days": self.p.target_cover_days,
                                    "capacity_room_before": self.room(st), "capacity": self.cap[st]},
                      "supply": {"warehouse_available_before": wh_before, "warehouse_available_after": wh_before - tot, "competing_stores": len(meta), "shortfall": sum(min(meta[s][2], meta[s][3]) for s in meta) > wh_before,
                                 "basis": basis, "note": sched_note, "method": "LP (HiGHS): maximise expected revenue" if lp is not None else "greedy water-filling",
                                 "expected_revenue_captured": None if lp is None else round(self.lp_gain.get((st, sku), 0.0))},
                      "checks": ["receiver within capacity", "in-transit counted once as arriving", "warehouse units allocated once", "lead time applied"]}
                self.commit(self.d.warehouse_id, st, sku, q, "WH_TO_STORE", reason, dispatch_day, ev, supply_basis=basis)
            supply[sku] = sup

    # ---------------------------------------------------------------- display minimum
    def display_pass(self):
        """Fill every store x category that is below its minimum display. Warehouse first, then eligible donors.
        Units are committed one at a time (so the ledger is always exact) and merged into one action per route afterwards."""
        wh_id = self.d.warehouse_id
        for st in sorted(self.stores.index):
            tier = self.stores.at[st, "tier"]
            for cat in sorted({c for (t_, c) in self.md if t_ == tier}):
                mn = self.md[(tier, cat)]
                before = self.cat_total[(st, cat)]
                if mn - before <= 0:
                    continue
                skus = sorted(s for s in self.prod_sku if self.items.at[s, "cat2"] == cat)
                n0 = len(self.actions)
                while mn - self.cat_total[(st, cat)] > 0 and self.room(st) > 0:
                    best = None
                    for s in skus:
                        pos = self.P.get((st, s)) or self._peek_virtual(st, s)
                        if pos.excluded:
                            continue
                        src = wh_id if self.wh.get(s, 0) > 0 else None
                        if src is None:
                            dn = self._best_donor(s, st)
                            src = dn.store if dn else None
                        if src is None:
                            continue
                        day = self.dispatch_day + (self.lead_wh(st) if src == wh_id else self.lead_s2s(src, st))
                        v = self.veff(pos, day)
                        if v <= EPS:
                            continue
                        key = (self.cover(pos, day), -v, s)
                        if best is None or key < best[0]:
                            best = (key, s, src, pos, day)
                    if best is None:
                        break
                    _, s, src, pos, day = best
                    disp = {"category": cat, "minimum": mn, "units_before_plan": before}
                    if src == wh_id:
                        ev = {"recipient": {**self._pos_snapshot(pos, day), "cover_before_days": _r(self.cover(pos, day)), "capacity_room_before": self.room(st), "capacity": self.cap[st]},
                              "display": disp, "supply": {"warehouse_available_before": self.wh[s], "basis": "on_hand"},
                              "checks": ["category display minimum", "receiver within capacity", "warehouse units allocated once"]}
                        self.wh[s] -= 1
                        self.commit(wh_id, st, s, 1, "WH_TO_STORE", "display_minimum", self.dispatch_day, ev)
                    else:
                        dp = self.P[(src, s)]
                        ev = self._evidence_transfer(dp, pos, 1, day)
                        ev["display"] = disp
                        self.commit(src, st, s, 1, "STORE_TO_STORE", "display_minimum", self.dispatch_day, ev)
                # merge unit actions on the same route/SKU
                merged, keep = {}, []
                for a in self.actions[n0:]:
                    k = (a["type"], a["source"], a["destination"], a["sku_id"])
                    if k in merged:
                        merged[k]["qty"] += 1
                        merged[k]["value_mrp"] += a["unit_mrp"]
                    else:
                        merged[k] = a
                        keep.append(a)
                self.actions[n0:] = keep
                for a in keep:
                    pos = self.P[(st, a["sku_id"])]
                    day = self.dispatch_day + a["lead_days"]
                    a["evidence"]["recipient"]["cover_after_days"] = _r(self.cover(pos, day))
                    a["evidence"]["display"]["units_after_plan"] = self.cat_total[(st, cat)]
                    if "supply" in a["evidence"]:
                        a["evidence"]["supply"]["warehouse_available_after"] = self.wh.get(a["sku_id"], 0)
                still = mn - self.cat_total[(st, cat)]
                if still > 0:
                    self.unresolved.append({"kind": "display_minimum_unmet", "store": st, "category": cat, "units": still,
                                            "detail": f"{st} {cat}: {self.cat_total[(st, cat)]} units vs minimum {mn}; no warehouse stock or eligible donor, or store at capacity"})

    def _peek_virtual(self, st, s):
        tier = self.stores.at[st, "tier"]
        pv = self.peer_tier.get((s, tier), self.peer_all.get(s, 0.0))
        S = 1.0 if self.p.use_ml_forecast else self.seas[self.stores.at[st, "state"]]["index"]
        return Pos(st, s, 0, 0, 0, None, 0, 0, 0, 0, pv, pv * S, S, "low", "no_evidence", True, pv, "same SKU peers (position not stocked here)", 0.0,
                   protected=(st, self.items.at[s, "style_id"]) in self.prot)

    def _best_donor(self, sku, dst):
        best = None
        for st in self.by_sku.get(sku, []):
            if st == dst:
                continue
            dp = self.P[(st, sku)]
            sv = self.donor_surplus(dp)
            if sv > 0:
                key = (self.lead_s2s(st, dst), -sv, st)
                if best is None or key < best[0]:
                    best = (key, dp)
        return best[1] if best else None

    # ---------------------------------------------------------------- over-capacity / ageing pull-back
    def pullback_to_wh(self, pos, q, reason, evidence_extra):
        wh_id = self.d.warehouse_id
        day = self.dispatch_day + self.lead_wh(pos.store)
        ev = {"donor": {**self._pos_snapshot(pos, self.dispatch_day + 2), "on_hand_before": pos.on_hand - pos.out, "on_hand_after": pos.on_hand - pos.out - q, "is_ageing": self.is_ageing(pos),
                        "protected_style": pos.protected}, "assumption": f"pull-back destination = {wh_id} (brief does not define it; configurable)",
              "checks": ["protected styles excluded", "category display minimum kept", "30-day cover kept (conservative, same test as transfers)"], **evidence_extra}
        self.commit(pos.store, wh_id, pos.sku, q, "PULL_BACK", reason, self.dispatch_day, ev)

    def capacity_pass(self):
        for st in sorted(self.stores.index):
            excess = self.store_load[st] - self.cap[st]
            if excess <= 0:
                continue
            start = excess
            cands = []
            for sku in self.by_store[st]:
                pos = self.P[(st, sku)]
                if pos.on_hand - pos.out <= 0:
                    continue
                v = self.veff(pos, self.dispatch_day + 2)
                cv = math.inf if v <= EPS else (pos.on_hand - pos.out) / v
                cands.append((-cv if cv != math.inf else -1e12, -(pos.age_days or 0), sku))
            for _, _, sku in sorted(cands):
                if excess <= 0:
                    break
                pos = self.P[(st, sku)]
                s = min(self.donor_surplus(pos, record_block=True), excess)
                if s <= 0:
                    continue
                moved = self.move_to_demand(pos, "over_capacity", qty_limit=s)
                excess -= moved
                rest = min(s - moved, excess)
                if rest > 0:
                    self.pullback_to_wh(pos, rest, "over_capacity", {"capacity": {"store": st, "capacity": self.cap[st], "excess_at_start": start}})
                    excess -= rest
            if excess > 0:
                self.unresolved.append({"kind": "over_capacity_unresolved", "store": st, "detail": f"{st} still {excess} units over capacity {self.cap[st]}: remaining stock is protected/needed for display or 30-day cover", "units": excess})

    def ageing_passes(self, relocate=True):
        pool = [p for p in self.P.values() if self.is_ageing(p)]
        pool.sort(key=lambda p: (-(p.age_days or 0), -(p.on_hand * self.items.at[p.sku, "mrp"]), p.store, p.sku))
        for pos in pool:
            if relocate:
                self.move_to_demand(pos, "ageing_relocation")
            else:
                s = self.donor_surplus(pos, record_block=True)
                if s >= self.p.pullback_min_units:
                    self.pullback_to_wh(pos, s, "ageing_pullback", {})

    # ---------------------------------------------------------------- need-driven store->store
    def rebalance_pass(self):
        needs = []
        for (st, sku), pos in self.P.items():
            day = self.dispatch_day + 2
            if self.need(pos, day) > 0:
                needs.append((self.cover(pos, day), st, sku))
        for _, st, sku in sorted(needs):
            pos = self.P[(st, sku)]
            for _ in range(10):
                day = self.dispatch_day + 2
                n, r = self.need(pos, day), self.room(st)
                if n <= 0 or r <= 0:
                    break
                dn = self._best_donor(sku, st)
                if dn is None:
                    break
                lead = self.lead_s2s(dn.store, st)
                day = self.dispatch_day + lead
                q = min(self.need(pos, day), self.room(st), self.donor_surplus(dn))
                if q <= 0:
                    break
                ev = self._evidence_transfer(dn, pos, q, day)
                self.commit(dn.store, st, sku, q, "STORE_TO_STORE", "surplus_rebalance", self.dispatch_day, ev)

    def record_unmet(self):
        for (st, sku), pos in self.P.items():
            day = self.dispatch_day + self.lead_wh(st)
            n = self.need(pos, day)
            if n > 0:
                why = []
                if self.room(st) <= 0:
                    why.append(f"{st} is at/over capacity")
                if self.wh.get(sku, 0) <= 0:
                    why.append("no warehouse stock")
                if self._best_donor(sku, st) is None:
                    why.append("no store has surplus above 30-day cover")
                self.unresolved.append({"kind": "unmet_demand", "store": st, "sku": sku, "units": n, "cover_days": _r(self.cover(pos, day)), "detail": f"{st}/{sku}: needs {n} units; " + "; ".join(why or ["limited by supply ordering"]),
                                        "velocity_effective": round(self.veff(pos, day), 4)})



def _r(x):
    return None if x is None or x == math.inf else round(float(x), 1)


def plan(d: Data, vel: pd.DataFrame, seas: dict) -> Planner:
    pl = Planner(d, vel, seas)
    wh = pl.wh
    pl.ageing_passes(relocate=True)                      # A
    pl.display_pass()                                    # B
    supply = {k: v for k, v in wh.items()}
    pl.wh_pass(supply, pl.dispatch_day, "on_hand")       # C
    pl.wh = {k: supply.get(k, 0) for k in wh}            # remaining on-hand after pass C
    pl.rebalance_pass()                                  # D
    pl.capacity_pass()                                   # E1
    pl.ageing_passes(relocate=False)                     # E2
    for dt in sorted(pl.wh_inbound):                     # F
        day = max(pl.dispatch_day, int((dt - pl.as_of).days))
        sup = defaultdict(int)
        for sku, q, status in pl.wh_inbound[dt]:
            if sku in pl.prod_sku:
                sup[sku] += q
        pl.wh_pass(sup, day, "planned_inbound", reason="scheduled_inbound", sched_note=f"depends on planned inbound of {dt.date()} (status Planned)")
    pl.record_unmet()
    return pl
