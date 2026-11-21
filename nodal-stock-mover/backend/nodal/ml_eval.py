"""Model evaluation suite: temporal back-test, cold-start (unseen store / unseen styles), calibration, tuning, importance.

Every number the UI shows about forecast quality comes from here. Run:  python -m nodal.cli --backtest
"""
from __future__ import annotations
import itertools, json, time
import numpy as np
import pandas as pd

from . import forecast as F
from .forecast import Panel, HORIZON


def _agg_wape(P: Panel, y, p, g, mask):
    ys = np.bincount(g[mask], weights=y[mask]); ps = np.bincount(g[mask], weights=p[mask])
    keep = ys + ps > 0
    return float(np.abs(ys - ps)[keep].sum() / max(ys[keep].sum(), 1e-9))


def score(P: Panel, y, p, active, alpha=None):
    out = {}
    for name, mask in (("active_pairs", active), ("all_pairs", np.ones_like(active, bool))):
        yy, pp = y[mask], p[mask]
        d = float(2 * np.sum(np.where(yy > 0, yy * np.log(np.maximum(yy, 1e-9) / np.maximum(pp, 1e-9)), 0) - (yy - pp)) / max(len(yy), 1))
        out[name] = {"wape": float(np.abs(yy - pp).sum() / max(yy.sum(), 1e-9)), "mae": float(np.abs(yy - pp).mean()), "bias": float(pp.sum() / max(yy.sum(), 1e-9)), "poisson_dev": d, "n": int(mask.sum())}
    out["store_category_wape"] = _agg_wape(P, y, p, P.g_store_cat, active)
    out["sku_total_wape"] = _agg_wape(P, y, p, P.g_sku, active)
    out["store_total_wape"] = _agg_wape(P, y, p, P.g_store, active)
    if alpha is not None:
        out["pi90_coverage"] = F.coverage(y[active], p[active], alpha, 0.9)
        out["pi80_coverage"] = F.coverage(y[active], p[active], alpha, 0.8)
        lo, hi = F.interval(p[active], alpha, 0.9)
        out["pi90_mean_width"] = float(np.mean(hi - lo))
    return out


def _fit_predict(P, T, params=None, exclude_stores=(), exclude_styles=(), cols=None, first=13):
    m, c = F.train(P, T - HORIZON, first_origin=first, params=params, exclude_stores=exclude_stores, exclude_styles=exclude_styles, feature_cols=cols)
    X = P.features(T)[c]
    return np.maximum(m.predict(X), 0), m, c


def tune(P: Panel, val_origins, grid, log=print):
    """Random/grid search on validation origins only (never the test origins)."""
    best, rows = None, []
    for params in grid:
        t0 = time.time(); devs = []
        for V in val_origins:
            pred, _, _ = _fit_predict(P, V, params, first=13)
            y = P.target(V); act = P.win(max(V - 52, 0), V) > 0
            yy, pp = y[act], pred[act]
            devs.append(float(2 * np.sum(np.where(yy > 0, yy * np.log(np.maximum(yy, 1e-9) / np.maximum(pp, 1e-9)), 0) - (yy - pp)) / len(yy)))
        d = float(np.mean(devs)); rows.append({"params": params, "val_poisson_dev": d, "seconds": round(time.time() - t0, 1)})
        log(f"  {params} -> dev {d:.4f} ({rows[-1]['seconds']}s)")
        if best is None or d < best[0]:
            best = (d, params)
    return best[1], rows


def permutation_importance(P: Panel, m, cols, T, n_repeat=2, top=15, seed=3):
    rng = np.random.default_rng(seed)
    X = P.features(T)[cols].copy(); y = P.target(T); act = P.win(max(T - 52, 0), T) > 0
    X, y = X[act], y[act]
    def dev(pred):
        pred = np.maximum(pred, 1e-9); return float(2 * np.sum(np.where(y > 0, y * np.log(np.maximum(y, 1e-9) / pred), 0) - (y - pred)) / len(y))
    base = dev(m.predict(X)); imp = {}
    for c in cols:
        ds = []
        for _ in range(n_repeat):
            Xp = X.copy(); Xp[c] = rng.permutation(Xp[c].values); ds.append(dev(m.predict(Xp)) - base)
        imp[c] = float(np.mean(ds))
    return sorted(imp.items(), key=lambda kv: -kv[1])[:top]


def reliability(P, y, mu, alpha, bins=10):
    """Calibration of P(demand >= 1): predicted probability vs observed frequency, by decile of predicted probability."""
    r, p = F.nb_pmf_params(mu, alpha) if alpha > 1e-4 else (None, None)
    p1 = 1 - (np.exp(-np.maximum(mu, 1e-6)) if alpha <= 1e-4 else (p ** r))
    qs = np.quantile(p1, np.linspace(0, 1, bins + 1)); idx = np.clip(np.searchsorted(qs, p1, side="right") - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.sum() >= 20:
            rows.append({"predicted": float(p1[m].mean()), "observed": float((y[m] >= 1).mean()), "n": int(m.sum())})
    ece = float(sum(r_["n"] * abs(r_["predicted"] - r_["observed"]) for r_ in rows) / max(sum(r_["n"] for r_ in rows), 1))
    brier = float(np.mean((p1 - (y >= 1)) ** 2))
    return {"bins": rows, "ece": ece, "brier": brier}


def run_suite(d, quick=False, log=print):
    t0 = time.time()
    P = Panel(d); W = P.W
    test_origins = [W - 24, W - 16, W - 8, W - 4]
    val_origins = [W - 64, W - 56, W - 48, W - 40, W - 32]   # spans a full year of seasons; all earlier than every test origin
    log(f"panel {P.n} pairs x {W} weeks ({time.time() - t0:.1f}s)")
    grid = [F.DEFAULT_PARAMS] if quick else [dict(F.DEFAULT_PARAMS, **kw) for kw in (
        {}, dict(learning_rate=0.03, max_iter=600), dict(max_leaf_nodes=48, min_samples_leaf=50), dict(l2_regularization=5.0, max_leaf_nodes=32), dict(max_leaf_nodes=16, min_samples_leaf=150))]
    log("tuning on validation origins", [str(P.week_end[v - 1].date()) for v in val_origins])
    best, trials = tune(P, val_origins, grid, log)
    # blend weight, bias scale and dispersion: fitted on VALIDATION origins only
    vm = []
    for V in val_origins:
        fc = F.Forecaster(best).fit(P, V - HORIZON)
        act = P.win(max(V - 52, 0), V) > 0
        vm.append((P.target(V)[act], fc.predict_ml_only(V)[act], F.base_peer(P, V)[act]))
    yv = np.concatenate([v[0] for v in vm]); mlv = np.concatenate([v[1] for v in vm]); peerv = np.concatenate([v[2] for v in vm])
    dev = lambda y, p: float(2 * np.sum(np.where(y > 0, y * np.log(np.maximum(y, 1e-9) / np.maximum(p, 1e-9)), 0) - (y - p)) / len(y))
    ws = [(dev(yv, np.maximum(w * mlv + (1 - w) * peerv, 1e-6)), w) for w in np.linspace(0, 1, 11)]
    # Blend weight and bias scale were examined on the validation origins but are NOT used: they are unstable across seasons
    # (early origins have < 1 year of training data and push the fitted scale to its clip limit), so the production model is pure ML.
    considered = {"blend_w_on_validation": float(min(ws)[1]), "scale_on_validation": float(np.clip(yv.sum() / max((min(ws)[1] * mlv + (1 - min(ws)[1]) * peerv).sum(), 1e-9), 0.8, 1.2))}
    w_best, scale = 1.0, 1.0
    alpha = F.fit_dispersion(yv, mlv)
    config = {"params": best, "blend_w": w_best, "scale": scale, "alpha": alpha, "considered_not_used": considered}
    log("config", config)

    temporal, importance, rel = [], None, None
    for T in test_origins:
        fc = F.Forecaster(best, w_best, scale, alpha).fit(P, T - HORIZON)
        y = P.target(T); act = P.win(max(T - 52, 0), T) > 0
        mu = fc.predict(T)
        base = {"current_trailing_rate": F.base_rate(P, T), "peer_smoothed": F.base_peer(P, T)}
        row = {"origin_week_end": str(P.week_end[T - 1].date()), "target_window": [str(P.origin_date(T).date()), str((P.origin_date(T) + pd.Timedelta(days=27)).date())], "actual_units": float(y[P.valid(T)].sum()),
               "models": {"ml_poisson_gbm": score(P, y, mu, act, alpha), "ml_only_uncalibrated": score(P, y, fc.predict_ml_only(T), act), **{k: score(P, y, v, act) for k, v in base.items()}}}
        temporal.append(row)
        m_ = row["models"]["ml_poisson_gbm"]
        log(f"  T={row['origin_week_end']}: wape {m_['active_pairs']['wape']:.3f} (cur {row['models']['current_trailing_rate']['active_pairs']['wape']:.3f}) bias {m_['active_pairs']['bias']:.2f} store-cat {m_['store_category_wape']:.3f} sku {m_['sku_total_wape']:.3f}")
        if T == test_origins[-1]:
            importance = permutation_importance(P, fc.model, fc.cols, T)
            rel = reliability(P, y[act], mu[act], alpha)

    stores = sorted(set(P.store))
    holdout_stores = [s for s in ("BLR-04", "KNP-02", "PAT-04", "MYS-01") if s in stores]
    styles = sorted(set(P.style)); rng = np.random.default_rng(11); holdout_styles = list(rng.choice(styles, size=max(1, len(styles) // 6), replace=False))
    cold = {"stores": holdout_stores, "styles": [str(x) for x in holdout_styles], "origins": []}
    for T in test_origins[-2:]:
        y = P.target(T); act = P.win(max(T - 52, 0), T) > 0
        r = {"origin_week_end": str(P.week_end[T - 1].date())}
        for kind, kw, mask in (("unseen_stores", dict(exclude_stores=holdout_stores), np.isin(P.store, holdout_stores)), ("unseen_styles", dict(exclude_styles=holdout_styles), np.isin(P.style, holdout_styles))):
            fc = F.Forecaster(best, w_best, scale, alpha).fit(P, T - HORIZON, **kw)
            mk = act & mask
            r[kind] = {"ml": score(P, y, fc.predict(T), mk, alpha), "peer_smoothed": score(P, y, F.base_peer(P, T), mk), "current_trailing_rate": score(P, y, F.base_rate(P, T), mk)}
        cold["origins"].append(r)
        log(f"  cold T={r['origin_week_end']}: stores {r['unseen_stores']['ml']['active_pairs']['wape']:.3f} vs peer {r['unseen_stores']['peer_smoothed']['active_pairs']['wape']:.3f}; styles {r['unseen_styles']['ml']['active_pairs']['wape']:.3f} vs peer {r['unseen_styles']['peer_smoothed']['active_pairs']['wape']:.3f}")

    stress = None
    To = int(np.searchsorted(P.week_end, pd.Timestamp("2025-10-20") - pd.Timedelta(days=14)))
    if To >= 20:
        fc = F.Forecaster(best, w_best, scale, alpha).fit(P, To - HORIZON, first_origin=8)
        y = P.target(To); act = P.win(max(To - 26, 0), To) > 0
        stress = {"origin_week_end": str(P.week_end[To - 1].date()), "ml": score(P, y, fc.predict(To), act, alpha), "current_trailing_rate": score(P, y, F.base_rate(P, To), act),
                  "note": "Diwali 2025 forecast using under one year of history: hard case, shown to be honest about festival-window accuracy"}
    report = {"horizon_weeks": HORIZON, "n_pairs": int(P.n), "n_weeks": int(W), "features": int(len(fc.cols)), "validation_origins": [str(P.week_end[v - 1].date()) for v in val_origins],
              "tuning": trials, "config": config, "blend_curve": [{"w": float(w), "dev": d_} for d_, w in ws], "origins": temporal, "cold_start": cold, "festival_stress": stress,
              "importance": importance, "reliability": rel, "seconds": round(time.time() - t0, 1)}
    report["scorecard"] = scorecard(report)
    return report


def scorecard(r):
    """Pre-declared trust checks (thresholds fixed before looking at results). Pass/fail is reported as measured."""
    o = r["origins"]; ml = lambda x: x["models"]["ml_poisson_gbm"]
    checks = [
        ("Beats trailing-rate baseline (Poisson deviance) at every test origin", all(ml(x)["active_pairs"]["poisson_dev"] < x["models"]["current_trailing_rate"]["active_pairs"]["poisson_dev"] for x in o)),
        ("Beats trailing-rate baseline (WAPE) at >= 3 of 4 origins", sum(ml(x)["active_pairs"]["wape"] < x["models"]["current_trailing_rate"]["active_pairs"]["wape"] for x in o) >= 3),
        ("Total-volume bias within +/-10% at every origin", all(abs(ml(x)["active_pairs"]["bias"] - 1) <= 0.10 for x in o)),
        ("Store x category WAPE <= 25% at >= 3 of 4 origins", sum(ml(x)["store_category_wape"] <= 0.25 for x in o) >= 3),
        ("Probability calibration error (ECE of P(demand>=1)) <= 0.03", (r["reliability"] or {}).get("ece", 1) <= 0.03),
        ("Unseen styles: not worse than peer baseline (mean WAPE)", np.mean([c["unseen_styles"]["ml"]["active_pairs"]["wape"] - c["unseen_styles"]["peer_smoothed"]["active_pairs"]["wape"] for c in r["cold_start"]["origins"]]) <= 0.0),
        ("Unseen stores: not worse than peer baseline (mean WAPE)", np.mean([c["unseen_stores"]["ml"]["active_pairs"]["wape"] - c["unseen_stores"]["peer_smoothed"]["active_pairs"]["wape"] for c in r["cold_start"]["origins"]]) <= 0.0),
    ]
    return [{"check": c, "passed": bool(p)} for c, p in checks]
