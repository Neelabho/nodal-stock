"""Demand forecasting v2: Poisson gradient-boosted model on a weekly store x SKU panel.

ML engineering choices (each one is tested or measured in ml_eval.py):
  * Cleaning: packaging excluded; rows before a store opened / a style launched are dropped (those zeros are not demand);
    weekly counts come from bill_lines joined to bills only.
  * Exposure-aware features: rates are computed over weeks the pair could actually sell (open + launched), so new stores and
    new styles are not penalised for history they never had.
  * No identity features: store/SKU/style IDs are never model inputs; only attributes (tier, state, size, capacity, price,
    category, size, launch age...) -> the model applies to unseen stores and unseen styles (cold start), verified by leave-out tests.
  * Ablation (ml_eval/experiments): year-ago features were dropped; with one year of look-back they are calendar-misaligned
    (festival dates move) and raised bias; festival effects enter through days-to-festival / wedding-calendar features instead.
  * Leakage-free: every feature at origin o uses weeks < o only (tested).
  * Normalisation: scale-free ratio / share / rate features next to raw counts; unseen categories are treated as missing.
  * Uncertainty: negative-binomial predictive distribution with a dispersion fitted on held-out origins (calibrated intervals).
Availability (days_in_stock) exists for 12 weeks only, so history is not availability-adjusted; the velocity layer floors
the forecast with the in-stock rate where a position was out of stock.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

from .loader import Data

HORIZON = 4  # weeks (28 days)
CATS = ["tier_c", "state_c", "zone_c", "cat_c", "size_c"]

DEFAULT_PARAMS = dict(learning_rate=0.05, max_iter=400, max_leaf_nodes=24, min_samples_leaf=80, l2_regularization=1.0, max_features=0.8)


class Panel:
    def __init__(self, d: Data):
        t = d.t
        items = t["items"].set_index("sku_id")
        prod = items[items.is_packaging == 0]
        sales = t["bill_lines"].merge(t["bills"][["bill_id", "bill_date_dt", "store_id"]], on="bill_id")
        sales = sales[sales.sku_id.isin(prod.index)]
        d0 = sales.bill_date_dt.min()
        first_sat = d0 + pd.Timedelta(days=(5 - d0.weekday()) % 7)
        self.week_end = pd.date_range(first_sat, d.as_of, freq="W-SAT")
        self.W = len(self.week_end)
        sales["w"] = ((sales.bill_date_dt - (self.week_end[0] - pd.Timedelta(days=6))).dt.days // 7).clip(lower=0)
        snap = t["stock_snapshot"]
        nonwh = snap.location_id != d.warehouse_id
        keys = set(zip(sales.store_id, sales.sku_id)) | set(zip(snap.location_id[nonwh], snap.sku_id[nonwh])) | set(zip(t["stock_weekly"].store_id, t["stock_weekly"].sku_id))
        self.pairs = pd.MultiIndex.from_tuples(sorted(keys), names=["store_id", "sku_id"])
        self.n = len(self.pairs)
        pidx = {k: i for i, k in enumerate(self.pairs)}
        U = np.zeros((self.n, self.W))
        for (s, k, w), q in sales.groupby(["store_id", "sku_id", "w"]).qty.sum().items():
            if w < self.W:
                U[pidx[(s, k)], w] = q
        self.U = U
        st = t["stores"].set_index("store_id")
        self.store = self.pairs.get_level_values(0).values
        self.sku = self.pairs.get_level_values(1).values
        codes = lambda col, src: pd.Categorical(src.reindex(self.sku if src is prod else self.store).values, categories=sorted(src[col].dropna().unique())).codes if False else None
        cat_levels = {c: {v: i for i, v in enumerate(sorted(set(series)))} for c, series in
                      (("tier", st.tier), ("state", st.state), ("zone", st.zone), ("category", prod.category), ("size", prod["size"]))}
        enc = lambda col, vals: np.array([cat_levels[col].get(v, -1) for v in vals])
        size_num = {"S": 1, "M": 2, "L": 3, "XL": 4, "XXL": 5, "XXXL": 6, "FS": 0}
        self.static = pd.DataFrame({
            "tier_c": enc("tier", st.tier.reindex(self.store).values), "state_c": enc("state", st.state.reindex(self.store).values),
            "zone_c": enc("zone", st.zone.reindex(self.store).values), "cat_c": enc("category", prod.category.reindex(self.sku).values),
            "size_c": enc("size", prod["size"].reindex(self.sku).values),
            "size_num": [size_num.get(s, float(s) - 5 if str(s).isdigit() else np.nan) for s in prod["size"].reindex(self.sku).values],
            "core": prod.is_core_style.reindex(self.sku).values, "mrp": prod.mrp.reindex(self.sku).values,
            "sqft": st.size_sqft.astype(float).reindex(self.store).values, "capacity": st.stock_capacity_units.reindex(self.store).values}, index=self.pairs)
        self.style = prod.style_id.reindex(self.sku).values
        self.state = st.state.reindex(self.store).values
        self.tier = st.tier.reindex(self.store).values
        self.cat = prod.category.reindex(self.sku).values
        self.launch = pd.to_datetime(prod.launch_date_dt.reindex(self.sku).values)
        self.opened = pd.to_datetime(st.opened_on_dt.reindex(self.store).values)
        fz = lambda a: pd.factorize(pd.Series(a).astype(str))[0]
        self.g_style_store = fz(pd.Series(self.store) + "|" + pd.Series(self.style))
        self.g_sku = fz(self.sku); self.g_sku_tier = fz(pd.Series(self.sku) + "|" + pd.Series(self.tier))
        self.g_store = fz(self.store); self.g_state = fz(self.state)
        self.g_store_cat = fz(pd.Series(self.store) + "|" + pd.Series(self.cat)); self.g_cat_tier = fz(pd.Series(self.cat) + "|" + pd.Series(self.tier))
        cal = t["calendar"]
        self.cal = {}
        for s in cal.state.unique():
            c = cal[cal.state == s]
            f = c[c.festival != ""]
            self.cal[s] = {"fest": np.sort(f.date_dt.values), "wed": np.sort(c[c.is_wedding_date == 1].date_dt.values),
                           "diwali": np.sort(f[f.festival.str.contains("Diwali", case=False)].date_dt.values)}
        self.C = np.concatenate([np.zeros((self.n, 1)), np.cumsum(U, axis=1)], axis=1)
        # exposure: weeks in which the pair could sell (store open and style launched)
        wk_start = np.array([self.week_end[0] + pd.Timedelta(days=7 * (w - 1) + 1) if w >= 0 else None for w in range(self.W)])
        starts = pd.DatetimeIndex([self.week_end[0] - pd.Timedelta(days=6) + pd.Timedelta(days=7 * w) for w in range(self.W)])
        ok_open = (self.opened.values[:, None] <= (starts.values[None, :] + np.timedelta64(6, "D")))
        ok_launch = (np.where(pd.isna(self.launch.values), np.datetime64("1970-01-01"), self.launch.values)[:, None] <= (starts.values[None, :] + np.timedelta64(6, "D")))
        self.A = (ok_open & ok_launch).astype(float)
        self.CA = np.concatenate([np.zeros((self.n, 1)), np.cumsum(self.A, axis=1)], axis=1)
        for name, a in (("e3", 0.3), ("e1", 0.1)):
            E = np.zeros((self.n, self.W + 1))
            for w in range(self.W):
                E[:, w + 1] = a * U[:, w] + (1 - a) * E[:, w]
            setattr(self, name, E)
        self._cache: dict[int, pd.DataFrame] = {}

    # ---- helpers
    def win(self, a, b, M=None):
        C = self.C if M is None else M
        if a < 0:
            return np.full(C.shape[0], np.nan)
        return C[:, b] - C[:, a]

    def agg(self, x, g):
        return np.bincount(g, weights=np.nan_to_num(x))[g]

    def origin_date(self, o):
        """First day of the target window for origin o (valid also for o == W, the live forecast)."""
        return self.week_end[0] - pd.Timedelta(days=6) + pd.Timedelta(days=7 * o)

    def cal_feats(self, o):
        start = self.origin_date(o); end = start + pd.Timedelta(days=7 * HORIZON - 1)
        s64, e64 = np.datetime64(start), np.datetime64(end)
        out = {}
        for s, c in self.cal.items():
            f, wd, dv = c["fest"], c["wed"], c["diwali"]
            nxt = f[f >= s64]; prv = f[f < s64]; dn = dv[dv >= s64]; dp = dv[dv < s64]
            day = np.timedelta64(1, "D")
            out[s] = (min(90, int((nxt[0] - s64) / day)) if len(nxt) else 90, min(90, int((s64 - prv[-1]) / day)) if len(prv) else 90,
                      int(((wd >= s64) & (wd <= e64)).sum()), int(((wd >= s64 - 28 * day) & (wd < s64)).sum()),
                      min(120, int((dn[0] - s64) / day)) if len(dn) else 120, min(120, int((s64 - dp[-1]) / day)) if len(dp) else 120,
                      int(((f >= s64) & (f <= e64)).sum()))
        return out

    def features(self, o) -> pd.DataFrame:
        if o in self._cache:
            return self._cache[o]
        P = self
        f = P.static.copy()
        ex = lambda k: np.maximum(P.CA[:, o] - P.CA[:, max(o - k, 0)], 0)
        for k in (1, 2, 4, 8, 13, 26, 52):
            f[f"u{k}"] = P.win(o - k, o) if o - k >= 0 else np.nan
        for k in (4, 13, 26):
            f[f"exp{k}"] = ex(k)
            f[f"rate{k}"] = np.nan_to_num(f[f"u{k}"].values) / np.maximum(ex(k), 1) if o - k >= 0 else np.nan
        f["ewm3"] = P.e3[:, o]; f["ewm1"] = P.e1[:, o]
        f["trend"] = (f["rate4"] + 0.05) / (f["rate13"] + 0.05)
        seg = P.U[:, max(0, o - 52):o] > 0
        last = np.where(seg.any(axis=1), seg.shape[1] - 1 - np.argmax(seg[:, ::-1], axis=1), -1)
        f["wk_since_sale"] = np.where(last < 0, 52, seg.shape[1] - 1 - last)
        f["sale_weeks13"] = (P.U[:, max(0, o - 13):o] > 0).sum(axis=1)
        for k in (4, 13):
            x = P.win(max(o - k, 0), o)
            n_active = lambda g: np.bincount(g, weights=(x > 0).astype(float))[g]
            f[f"style_store{k}"] = P.agg(x, P.g_style_store)
            f[f"sku_all{k}"] = P.agg(x, P.g_sku)
            f[f"sku_tier{k}_mean"] = P.agg(x, P.g_sku_tier) / np.maximum(np.bincount(P.g_sku_tier)[P.g_sku_tier], 1)
            f[f"store_tot{k}"] = P.agg(x, P.g_store)
            f[f"store_cat{k}"] = P.agg(x, P.g_store_cat)
            f[f"cat_tier{k}_mean"] = P.agg(x, P.g_cat_tier) / np.maximum(np.bincount(P.g_cat_tier)[P.g_cat_tier], 1)
        u13 = np.nan_to_num(f["u13"].values)
        f["store_share13"] = u13 / np.maximum(f["store_tot13"].values, 1)
        f["size_share13"] = u13 / np.maximum(f["style_store13"].values, 1)
        f["vs_peer13"] = (u13 + 0.5) / (f["sku_tier13_mean"].values + 0.5)
        cf = P.cal_feats(o)
        for j, name in enumerate(["d_next_fest", "d_prev_fest", "weddings_win", "weddings_prev", "d_next_diwali", "d_prev_diwali", "fests_win"]):
            f[name] = [cf[s][j] for s in P.state]
        d0 = P.origin_date(o)
        f["woy"] = d0.isocalendar()[1]
        f["launch_wk"] = np.clip(np.where(pd.isna(P.launch), 150, (d0 - P.launch).days / 7), -8, 150)
        f["store_wk"] = np.clip((d0 - P.opened).days / 7, -8, 400)
        f = f.astype("float32")
        P._cache[o] = f
        return f

    def valid(self, o):
        """Rows that could sell in the first target week (store open and style launched)."""
        d0 = self.origin_date(o)
        lim = d0 + pd.Timedelta(days=6)
        return np.asarray((self.opened <= lim) & (pd.isna(self.launch) | (self.launch <= lim)))

    def target(self, o):
        return self.win(o, o + HORIZON)


def make_model(params=None, **kw):
    p = {**DEFAULT_PARAMS, **(params or {}), **kw}
    return HistGradientBoostingRegressor(loss="poisson", early_stopping=False, random_state=7, categorical_features=None, **p)


def design(P: Panel, origins, exclude_stores=(), exclude_styles=()):
    X, y = [], []
    for o in origins:
        if o + HORIZON > P.W:
            continue
        m = P.valid(o)
        if exclude_stores:
            m &= ~np.isin(P.store, list(exclude_stores))
        if exclude_styles:
            m &= ~np.isin(P.style, list(exclude_styles))
        X.append(P.features(o)[m]); y.append(P.target(o)[m])
    return pd.concat(X), np.concatenate(y)


def train(P: Panel, last_origin: int, first_origin: int = 13, params=None, step=1, exclude_stores=(), exclude_styles=(), feature_cols=None):
    X, y = design(P, range(first_origin, last_origin + 1, step), exclude_stores, exclude_styles)
    cols = feature_cols or [c for c in X.columns if X[c].notna().any() and X[c].nunique() > 1]   # drop features with no information in this training window
    m = make_model(params).fit(X[cols], y)
    return m, cols


# ---------------------------------------------------------------- baselines
def base_rate(P, o, weeks=12):
    return P.win(max(o - weeks, 0), o) * HORIZON / min(weeks, o)


def base_peer(P, o, m=0.5, weeks=13):
    u = P.win(max(o - weeks, 0), o)
    active = (P.win(max(o - 52, 0), o) > 0).astype(float)
    g = P.g_sku_tier
    prior = np.bincount(g, weights=u * active)[g] / np.maximum(np.bincount(g, weights=active)[g], 1)
    return ((u + m * prior) / (1 + m)) * HORIZON / min(weeks, o)


# ---------------------------------------------------------------- uncertainty
def nb_pmf_params(mu, alpha):
    mu = np.maximum(mu, 1e-6)
    r = 1.0 / alpha
    return r, r / (r + mu)


def fit_dispersion(y, mu):
    """MLE of the NB2 dispersion alpha on held-out data (var = mu + alpha mu^2)."""
    grid = np.concatenate([[1e-4], np.geomspace(0.02, 5, 40)])
    best = max(grid, key=lambda a: stats.nbinom.logpmf(y, *nb_pmf_params(mu, a)).sum() if a > 1e-4 else stats.poisson.logpmf(y, np.maximum(mu, 1e-6)).sum())
    return float(best)


def interval(mu, alpha, level=0.9):
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    if alpha <= 1e-4:
        return stats.poisson.ppf(lo_q, np.maximum(mu, 1e-6)), stats.poisson.ppf(hi_q, np.maximum(mu, 1e-6))
    r, p = nb_pmf_params(mu, alpha)
    return stats.nbinom.ppf(lo_q, r, p), stats.nbinom.ppf(hi_q, r, p)


def coverage(y, mu, alpha, level=0.9):
    lo, hi = interval(mu, alpha, level)
    return float(np.mean((y >= lo) & (y <= hi)))


# ---------------------------------------------------------------- final forecaster (config chosen on validation origins only)
class Forecaster:
    """ML + peer-smoothed blend, bias-recalibrated, with negative-binomial uncertainty. Config comes from ml_eval validation."""

    def __init__(self, params=None, blend_w=1.0, scale=1.0, alpha=0.05):
        self.params, self.w, self.scale, self.alpha = params or DEFAULT_PARAMS, blend_w, scale, alpha

    def fit(self, P: Panel, last_origin: int, exclude_stores=(), exclude_styles=(), first_origin=13):
        self.P = P
        self.model, self.cols = train(P, last_origin, first_origin=first_origin, params=self.params, exclude_stores=exclude_stores, exclude_styles=exclude_styles)
        return self

    def predict(self, o: int):
        ml = np.maximum(self.model.predict(self.P.features(o)[self.cols]), 0)
        peer = base_peer(self.P, o) if o <= self.P.W else ml
        return self.scale * (self.w * ml + (1 - self.w) * peer)

    def predict_ml_only(self, o):
        return np.maximum(self.model.predict(self.P.features(o)[self.cols]), 0)

    def interval(self, mu, level=0.9):
        return interval(mu, self.alpha, level)


def load_config() -> dict:
    """Forecast config (params, blend, scale, alpha) chosen on validation origins by ml_eval; defaults if the report is absent."""
    import json
    from pathlib import Path
    rp = Path(__file__).resolve().parents[2] / "docs" / "FORECAST_BACKTEST.json"
    if rp.exists():
        try:
            return json.loads(rp.read_text()).get("config", {})
        except Exception:
            return {}
    return {}
