"""Velocity engine: availability-adjusted sales rate per store x SKU, peer shrinkage, festival seasonality.

All inputs are traced to source rows:  units = sum(bill_lines.qty) in window,  in-stock days = sum(stock_weekly.days_in_stock).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from .loader import Data


def _sales(d: Data) -> pd.DataFrame:
    t = d.t
    prod = t["items"].loc[t["items"].is_packaging == 0, ["sku_id", "style_id", "cat2"]]
    s = t["bill_lines"].merge(t["bills"][["bill_id", "bill_date_dt", "store_id"]], on="bill_id").merge(prod, on="sku_id")
    return s.rename(columns={"bill_date_dt": "date"})


def season_index(d: Data, sales: pd.DataFrame) -> dict:
    """Per state: uplift = (units/day in the next H days, one year ago at the same offset from the same festival)
    / (units/day in the 84 days before that point). Derived only from sales + calendar."""
    p, cal = d.policy, d.t["calendar"]
    stores = d.t["stores"]
    st_of = stores.set_index("store_id").state
    sales = sales.assign(state=sales.store_id.map(st_of))
    daily = sales.groupby(["state", "date"]).qty.sum()
    W, H = p.velocity_window_days, p.seasonality_horizon_days
    dmin = sales.date.min()
    out = {}
    for state in sorted(stores.state.unique()):
        info = {"index": 1.0, "festival": None, "festival_date": None, "offset_days": None, "note": "no festival within horizon", "thin": False}
        c = cal[(cal.state == state) & (cal.festival != "") & (cal.date_dt > d.as_of)].sort_values("date_dt")
        c = c[(c.date_dt - d.as_of).dt.days <= p.seasonality_max_offset_days]
        if len(c):
            f = c.iloc[0]
            off = int((f.date_dt - d.as_of).days)
            prev = cal[(cal.state == state) & (cal.festival == f.festival) & (cal.date_dt < f.date_dt - pd.Timedelta(days=300)) & (cal.date_dt > f.date_dt - pd.Timedelta(days=430))]
            info.update(festival=f.festival, festival_date=str(f.date_dt.date()), offset_days=off)
            if len(prev):
                analog = prev.iloc[0].date_dt - pd.Timedelta(days=off)
                b0, b1 = analog - pd.Timedelta(days=W - 1), analog
                w0, w1 = analog + pd.Timedelta(days=1), analog + pd.Timedelta(days=H)
                if b0 >= dmin:
                    ser = daily.loc[state]
                    base = ser[(ser.index >= b0) & (ser.index <= b1)].sum() / W
                    win = ser[(ser.index >= w0) & (ser.index <= w1)].sum() / H
                    if base > 0:
                        raw = win / base
                        lo, hi = p.seasonality_clip
                        info.update(index=float(np.clip(raw, lo, hi)), raw=float(raw), analog_festival_date=str(prev.iloc[0].date_dt.date()),
                                    baseline_window=[str(b0.date()), str(b1.date())], uplift_window=[str(w0.date()), str(w1.date())],
                                    baseline_units_per_day=float(base), uplift_units_per_day=float(win),
                                    thin=bool(base * W < 500),
                                    note=f"{f.festival} on {f.date_dt.date()} (in {off}d): last year's same-offset window sold {raw:.2f}x the trailing-{W}d rate")
                    else:
                        info["note"] = "no baseline sales last year; index 1.0"
                else:
                    info["note"] = "sales history too short for last-year baseline; index 1.0"
            else:
                info["note"] = f"{f.festival} upcoming but no prior-year occurrence in calendar; index 1.0"
        out[state] = info
    return out


def compute_velocity(d: Data):
    p, t = d.policy, d.t
    W = p.velocity_window_days
    sales = _sales(d)
    w0 = d.as_of - pd.Timedelta(days=W - 1)
    units = sales[(sales.date >= w0) & (sales.date <= d.as_of)].groupby(["store_id", "sku_id"]).qty.sum().rename("units")
    avail = t["stock_weekly"].groupby(["store_id", "sku_id"]).days_in_stock.sum().rename("avail_days")
    snap_keys = t["stock_snapshot"].loc[t["stock_snapshot"].location_id != d.warehouse_id, ["location_id", "sku_id"]].rename(columns={"location_id": "store_id"})
    idx = pd.MultiIndex.from_frame(snap_keys).union(units.index).union(avail.index)
    v = pd.DataFrame(index=idx).join(units).join(avail).fillna(0)
    v[["units", "avail_days"]] = v[["units", "avail_days"]].astype(int)
    v = v.reset_index()
    st, it = t["stores"].set_index("store_id"), t["items"].set_index("sku_id")
    v["tier"] = v.store_id.map(st.tier)
    v["state"] = v.store_id.map(st.state)
    elig = (d.as_of - v.store_id.map(st.opened_on_dt)).dt.days + 1
    lch = (d.as_of - v.sku_id.map(it.launch_date_dt)).dt.days + 1
    v["elig_days"] = np.minimum(W, np.minimum(elig, lch.fillna(W))).astype(int).clip(lower=1)
    # Inference: if the oldest unit on hand arrived N days ago, the position has held stock on every day since (the oldest unit is still here).
    sn = t["stock_snapshot"]
    sn = sn[(sn.location_id != d.warehouse_id) & (sn.on_hand > 0)].set_index(["location_id", "sku_id"])
    sn.index.names = ["store_id", "sku_id"]
    age = (d.as_of - sn.oldest_stock_received_on_dt).dt.days + 1
    v = v.join(age.rename("inferred_days"), on=["store_id", "sku_id"])
    v["weekly_days"] = np.minimum(v.avail_days, v.elig_days)
    v["avail_days"] = np.minimum(np.maximum(v.avail_days, v.inferred_days.fillna(0).astype(int)), v.elig_days)
    a_eff = np.where(v.avail_days > 0, v.avail_days, np.where(v.units > 0, v.elig_days, 0))
    v["evidence_days"] = a_eff
    v["obs_vel"] = np.where(a_eff > 0, v.units / np.maximum(a_eff, 1), 0.0)

    # peer rate: same SKU, same tier (then same SKU any tier), only from positions with solid evidence
    solid = v[v.evidence_days >= p.min_evidence_days]
    g_t = solid.groupby(["sku_id", "tier"]).apply(lambda g: g.units.sum() / g.evidence_days.sum(), include_groups=False).rename("peer_tier")
    g_a = solid.groupby("sku_id").apply(lambda g: g.units.sum() / g.evidence_days.sum(), include_groups=False).rename("peer_all")
    v = v.join(g_t, on=["sku_id", "tier"]).join(g_a, on="sku_id")
    v["peer_vel"] = v.peer_tier.fillna(v.peer_all).fillna(0.0)
    v["peer_src"] = np.where(v.peer_tier.notna(), "same SKU, same tier", np.where(v.peer_all.notna(), "same SKU, all tiers", "none"))
    thin = v.evidence_days < p.min_evidence_days
    v["vel"] = np.where(thin, (v.units + p.shrink_k_days * v.peer_vel) / (v.evidence_days + p.shrink_k_days), v.obs_vel)
    v["shrunk"] = thin

    # zero sales is only trusted ("proven slow") after >= min_evidence_days of proven availability; otherwise it is censored / unknown
    v["zero_class"] = np.where(v.units > 0, "has_sales",
                      np.where(v.avail_days == 0, "no_evidence", np.where(v.avail_days >= p.min_evidence_days, "proven_slow", "censored")))
    v["confidence"] = np.where(v.evidence_days < 21, "low",
                      np.where((v.evidence_days >= p.min_evidence_days) & ((v.units >= 3) | (v.zero_class == "proven_slow")), "high", "medium"))
    seas = season_index(d, sales)
    v["season"] = v.state.map(lambda s: seas[s]["index"])
    v["vel_base"] = v.vel
    v["vel_eff_base"] = v.vel * v.season
    v["vel_ml"] = np.nan
    v["floor_applied"] = False
    v = v.set_index(["store_id", "sku_id"])
    if p.use_ml_forecast:
        from .forecast import Panel, Forecaster, HORIZON, DEFAULT_PARAMS
        import json
        from pathlib import Path
        cfg = {}
        rp = Path(__file__).resolve().parents[2] / "docs" / "FORECAST_BACKTEST.json"
        if rp.exists():
            try:
                cfg = json.loads(rp.read_text()).get("config", {})   # chosen on validation origins only (ml_eval)
            except Exception:
                cfg = {}
        P = Panel(d)
        fc = Forecaster(cfg.get("params", DEFAULT_PARAMS), cfg.get("blend_w", 1.0), cfg.get("scale", 1.0), cfg.get("alpha", 0.05)).fit(P, P.W - HORIZON)
        mu = fc.predict(P.W)                                         # expected units over the next 28 days
        lo, hi = fc.interval(mu, 0.9)
        lam = pd.Series(mu / (7 * HORIZON), index=P.pairs)           # daily rate
        v["vel_ml"] = lam.reindex(v.index).values
        v["fc_lo"] = pd.Series(lo, index=P.pairs).reindex(v.index).values
        v["fc_hi"] = pd.Series(hi, index=P.pairs).reindex(v.index).values
        has = v.vel_ml.notna()
        # in-stock floor: history is not availability-adjusted, so for positions that were out of stock part of the window,
        # never forecast below the in-stock rate x festival index (stock-outs must not hide demand)
        cens = has & (v.avail_days < 0.8 * v.elig_days) & (v.units > 0)
        v["floor_applied"] = cens & (v.vel_eff_base > v.vel_ml)
        eff = np.where(has, np.where(cens, np.maximum(v.vel_ml, v.vel_eff_base), v.vel_ml), v.vel_eff_base)
        v["vel"] = eff
        v["season"] = 1.0                      # festival effect is inside the forecast (features: days to festival, last year's window)
        v["vel_eff"] = eff
    else:
        v["vel_eff"] = v.vel * v.season
    return v, seas
