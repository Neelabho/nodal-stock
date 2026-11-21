"""NODAL policy layer.

Every business parameter lives here, tagged with its source:
  SOURCE   -> stated in the Manyavar brief / CSVs (re-read from the CSVs at runtime where possible)
  ASSUMED  -> a documented engineering assumption (NOT Manyavar policy); change here, shown in UI
"""
from __future__ import annotations
from dataclasses import dataclass, asdict

ENGINE_VERSION = "0.1.0"


@dataclass(frozen=True)
class Policy:
    # --- SOURCE (overridden from rules.csv at load time when parseable) ---
    donor_min_cover_days: int = 30          # rules.csv "Donor cover" / brief
    approval_value_mrp: int = 100_000       # rules.csv "Approval": > Rs 1,00,000 at MRP needs human approval
    # --- ASSUMED ---
    dispatch_offset_days: int = 1           # plan dispatches the day after the snapshot (31 Oct -> 1 Nov)
    use_ml_forecast: bool = True            # Poisson GBM demand forecast (forecast.py); False = trailing in-stock rate x festival index
    use_optimizer: bool = True              # warehouse allocation by LP (maximise expected sales) instead of greedy cover-equalising
    velocity_window_days: int = 84          # = 12 weeks, the span of stock_weekly availability evidence
    min_evidence_days: int = 42             # below this many in-stock days, shrink toward peer rate
    shrink_k_days: int = 14                 # prior strength (in pseudo in-stock days) for peer shrinkage
    reorder_trigger_cover_days: int = 14    # under-stocked if projected cover at arrival is below this
    target_cover_days: int = 30             # order-up-to cover at arrival
    seasonality_horizon_days: int = 30      # window over which last year's festival uplift is measured
    seasonality_max_offset_days: int = 30   # only apply a festival due within this many days
    seasonality_clip: tuple = (0.5, 3.0)
    ageing_min_age_days: int = 180          # oldest unit at least this old
    ageing_min_cover_days: int = 180        # ... and cover at least this (or proven-zero velocity)
    pullback_destination: str = "WH-KOL"    # assumption: where non-sellable ageing/over-capacity stock goes
    pullback_min_units: int = 2             # materiality floor for ageing pull-backs (units per position)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["seasonality_clip"] = list(self.seasonality_clip)
        return d


SOURCES = {
    "donor_min_cover_days": "SOURCE: rules.csv 'Donor cover'",
    "approval_value_mrp": "SOURCE: rules.csv 'Approval'",
    "dispatch_offset_days": "ASSUMED",
    "use_ml_forecast": "ASSUMED: validated by rolling-origin back-test (docs/FORECAST_BACKTEST.json)",
    "use_optimizer": "ASSUMED: objective = expected revenue captured at MRP; hard rules unchanged",
    "velocity_window_days": "ASSUMED: matches 12-week stock_weekly evidence",
    "min_evidence_days": "ASSUMED",
    "shrink_k_days": "ASSUMED",
    "reorder_trigger_cover_days": "ASSUMED",
    "target_cover_days": "ASSUMED (brief only fixes the 30-day donor floor)",
    "seasonality_horizon_days": "ASSUMED",
    "seasonality_max_offset_days": "ASSUMED",
    "seasonality_clip": "ASSUMED",
    "ageing_min_age_days": "ASSUMED",
    "ageing_min_cover_days": "ASSUMED",
    "pullback_destination": "CONFIRMED by the builder: a store that sells the SKU first, otherwise the warehouse (the brief does not define it)",
    "pullback_min_units": "ASSUMED",
}
