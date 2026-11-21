import json
from pathlib import Path
import numpy as np
from nodal.forecast import Panel, HORIZON

BT = Path(__file__).resolve().parents[1] / "docs" / "FORECAST_BACKTEST.json"


def test_no_leakage_features_ignore_the_future(data):
    P = Panel(data)
    o = P.W - 12
    before = P.features(o)
    U2 = P.U.copy(); U2[:, o:] = 0                      # erase everything the model must not see
    P.U = U2; P.C = np.concatenate([np.zeros((P.n, 1)), np.cumsum(U2, axis=1)], axis=1)
    after = P.features(o)
    assert before.fillna(-1).equals(after.fillna(-1))


def test_panel_units_match_bill_lines(data):
    P = Panel(data)
    items = data.t["items"]
    prod = set(items.loc[items.is_packaging == 0, "sku_id"])
    total = int(data.t["bill_lines"][data.t["bill_lines"].sku_id.isin(prod)].qty.sum())
    assert int(P.U.sum()) == total


def test_ml_beats_current_method_in_backtest():
    bt = json.loads(BT.read_text())
    wins = 0
    for o in bt["origins"]:
        m = o["models"]
        assert m["ml_poisson_gbm"]["active_pairs"]["poisson_dev"] < m["current_trailing_rate"]["active_pairs"]["poisson_dev"]
        wins += m["ml_poisson_gbm"]["active_pairs"]["wape"] <= m["current_trailing_rate"]["active_pairs"]["wape"]
    assert wins >= 3


def test_model_never_sees_identity_features(data):
    from nodal.forecast import Panel
    cols = set(Panel(data).features(60).columns)
    assert not {c for c in cols if c in ("store_c", "sku_c", "style_c") or "store_id" in c or "sku_id" in c}


def test_scorecard_is_reported_not_forced(data):
    bt = json.loads(BT.read_text())
    assert len(bt["scorecard"]) >= 7 and all(isinstance(c["passed"], bool) for c in bt["scorecard"])
    assert bt["config"]["blend_w"] == 1.0 and bt["config"]["scale"] == 1.0   # no unstable post-hoc calibration knobs in production
