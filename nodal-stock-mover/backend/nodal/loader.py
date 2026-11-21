"""Read-only CSV loader with schema validation, typing and policy extraction.
Source files are never modified. IDs stay text."""
from __future__ import annotations
import hashlib, re
from dataclasses import dataclass, field, replace
from pathlib import Path
import pandas as pd

from .policy import Policy

REQUIRED = {
    "stores": ["store_id", "city", "state", "zone", "tier", "stock_capacity_units", "opened_on"],
    "items": ["sku_id", "style_id", "style_name", "category", "size", "mrp", "launch_date", "is_core_style", "is_packaging"],
    "bills": ["bill_id", "bill_date", "store_id"],
    "bill_lines": ["bill_id", "sku_id", "qty", "mrp", "selling_price"],
    "stock_snapshot": ["as_of_date", "location_id", "sku_id", "on_hand", "in_transit", "in_transit_arrives_on", "oldest_stock_received_on"],
    "stock_weekly": ["week_ending", "store_id", "sku_id", "closing_on_hand", "days_in_stock"],
    "warehouse_inbound": ["expected_date", "sku_id", "qty", "status"],
    "calendar": ["date", "state", "festival", "is_wedding_date"],
    "rules": ["rule", "detail"],
    "min_display": ["tier", "category", "min_display_units"],
    "lead_times": ["route", "days"],
    "flagship_protected": ["store_id", "rank", "style_id"],
}


class DataError(Exception):
    """Raised with an actionable message when required data is missing or malformed."""


@dataclass
class Data:
    t: dict                      # name -> typed DataFrame
    as_of: pd.Timestamp
    warehouse_id: str
    policy: Policy
    policy_notes: list
    cat_map: dict                # item category -> min_display category
    lead_wh: dict                # zone -> days (warehouse -> store)
    lead_s2s: dict               # same_city / same_zone / diff_zone -> days
    source_hash: str
    source_dir: str
    manifest_files: dict = field(default_factory=dict)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def parse_lead_times(df: pd.DataFrame):
    wh, s2s = {}, {}
    for _, r in df.iterrows():
        route, days = r["route"], int(r["days"])
        m = re.search(r"Warehouse\s+(\S+)\s+to store,\s+(\w+)\s+zone", route, re.I)
        if m:
            wh[m.group(2).title()] = days
            continue
        low = route.lower()
        if "same city" in low:
            s2s["same_city"] = days
        elif "same zone" in low:
            s2s["same_zone"] = days
        elif "different zone" in low:
            s2s["diff_zone"] = days
    if not wh or len(s2s) != 3:
        raise DataError(f"lead_times.csv: could not parse routes (found WH zones={list(wh)}, store-to-store={list(s2s)}). "
                        "Expected 'Warehouse WH-xxx to store, <Zone> zone' and 'Store to store, same city / same zone... / different zone'.")
    return wh, s2s


def parse_cat_map(items: pd.DataFrame, min_display: pd.DataFrame) -> dict:
    """Map item categories onto min_display categories, e.g. 'Safa' -> 'Accessories (Safa/Mala/Stole)'."""
    m = {}
    for md_cat in min_display["category"].unique():
        g = re.search(r"\(([^)]+)\)", md_cat)
        if g:
            for tok in g.group(1).split("/"):
                m[tok.strip()] = md_cat
    for c in items["category"].unique():
        m.setdefault(c, c)
    return m


def load_all(data_dir: str | Path) -> Data:
    d = Path(data_dir)
    if not d.is_dir():
        raise DataError(f"Data directory not found: {d}")
    t, hashes = {}, {}
    for name, cols in REQUIRED.items():
        p = d / f"{name}.csv"
        if not p.exists():
            raise DataError(f"Required file missing: {p.name} (needed for Brief B). Found: {sorted(x.name for x in d.glob('*.csv'))}")
        df = _read(p)
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise DataError(f"{p.name}: missing required columns {missing}; has {list(df.columns)}")
        t[name] = df
        hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    src_hash = hashlib.sha256("".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()).hexdigest()[:16]

    def num(df, cols, kind="int"):
        for c in cols:
            try:
                df[c] = pd.to_numeric(df[c]).astype("int64" if kind == "int" else "float64")
            except Exception as e:
                bad = df.loc[pd.to_numeric(df[c], errors="coerce").isna(), c].unique()[:5]
                raise DataError(f"Column {c} contains non-numeric values e.g. {list(bad)}") from e

    def dt(df, cols):
        for c in cols:
            df[c + "_dt"] = pd.to_datetime(df[c].replace("", None), errors="coerce")

    num(t["stores"], ["stock_capacity_units"]); dt(t["stores"], ["opened_on"])
    num(t["items"], ["mrp", "is_core_style", "is_packaging"]); dt(t["items"], ["launch_date"])
    num(t["bill_lines"], ["qty", "mrp", "selling_price"]); dt(t["bills"], ["bill_date"])
    num(t["stock_snapshot"], ["on_hand", "in_transit"]); dt(t["stock_snapshot"], ["as_of_date", "in_transit_arrives_on", "oldest_stock_received_on"])
    num(t["stock_weekly"], ["closing_on_hand", "days_in_stock"]); dt(t["stock_weekly"], ["week_ending"])
    num(t["warehouse_inbound"], ["qty"]); dt(t["warehouse_inbound"], ["expected_date"])
    num(t["min_display"], ["min_display_units"]); num(t["calendar"], ["is_wedding_date"]); dt(t["calendar"], ["date"])
    num(t["flagship_protected"], ["rank"])

    snap = t["stock_snapshot"]
    dates = snap["as_of_date_dt"].dropna().unique()
    if len(dates) != 1:
        raise DataError(f"stock_snapshot must have a single as_of_date; found {len(dates)}")
    as_of = pd.Timestamp(dates[0])
    wh_ids = sorted(set(snap.location_id) - set(t["stores"].store_id))
    if len(wh_ids) != 1:
        raise DataError(f"Expected exactly one non-store location in stock_snapshot (the warehouse); found {wh_ids}")

    lead_wh, lead_s2s = parse_lead_times(t["lead_times"])
    cat_map = parse_cat_map(t["items"], t["min_display"])
    t["items"]["cat2"] = t["items"]["category"].map(cat_map)

    # policy values that the CSVs state explicitly
    pol, notes = Policy(), []
    for _, r in t["rules"].iterrows():
        if r["rule"].strip().lower() == "approval":
            m = re.search(r"Rs\s*([\d,]+)", r["detail"])
            if m:
                pol = replace(pol, approval_value_mrp=int(m.group(1).replace(",", ""))); notes.append(f"approval threshold read from rules.csv: {pol.approval_value_mrp}")
        if r["rule"].strip().lower() == "donor cover":
            m = re.search(r"(\d+)\s*days", r["detail"])
            if m:
                pol = replace(pol, donor_min_cover_days=int(m.group(1))); notes.append(f"donor cover read from rules.csv: {pol.donor_min_cover_days} days")
    pol = replace(pol, pullback_destination=wh_ids[0])
    missing_tiers = set(t["stores"].tier) - set(t["min_display"].tier)
    if missing_tiers:
        raise DataError(f"min_display has no rows for store tiers {sorted(missing_tiers)}")
    return Data(t=t, as_of=as_of, warehouse_id=wh_ids[0], policy=pol, policy_notes=notes, cat_map=cat_map,
                lead_wh=lead_wh, lead_s2s=lead_s2s, source_hash=src_hash, source_dir=str(d), manifest_files=hashes)
