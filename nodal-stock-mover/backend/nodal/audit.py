"""Data audit: table profiles + data-quality findings, all computed from the loaded CSVs."""
from __future__ import annotations
import pandas as pd
from .loader import Data


def profile_tables(d: Data) -> list:
    out = []
    for name, df in d.t.items():
        cols = [c for c in df.columns if not c.endswith("_dt")]
        nulls = {c: int((df[c].astype(str) == "").sum()) for c in cols if (df[c].astype(str) == "").sum() > 0}
        out.append({"table": name, "rows": int(len(df)), "columns": len(cols), "blank_cells": nulls,
                    "duplicate_rows": int(df[cols].duplicated().sum())})
    return out


def findings(d: Data) -> list:
    t, pol = d.t, d.policy
    F = []

    def add(sev, table, check, count, detail, handling):
        F.append({"severity": sev, "table": table, "check": check, "count": int(count), "detail": detail, "handling": handling})

    stores, items, snap, sw = t["stores"], t["items"], t["stock_snapshot"], t["stock_weekly"]
    bills, bl, inb = t["bills"], t["bill_lines"], t["warehouse_inbound"]

    # --- integrity
    for tbl, key in [("stores", ["store_id"]), ("items", ["sku_id"]), ("bills", ["bill_id"]),
                     ("stock_snapshot", ["location_id", "sku_id"]), ("stock_weekly", ["week_ending", "store_id", "sku_id"]),
                     ("warehouse_inbound", ["expected_date", "sku_id"])]:
        n = int(t[tbl].duplicated(key).sum())
        add("ok" if n == 0 else "warn", tbl, f"duplicate key {key}", n, "No duplicate keys." if n == 0 else "Duplicate keys found.",
            "none needed" if n == 0 else "first occurrence kept")
    fk = {
        "bill_lines.bill_id -> bills": (~bl.bill_id.isin(bills.bill_id)).sum(),
        "bill_lines.sku_id -> items": (~bl.sku_id.isin(items.sku_id)).sum(),
        "bills.store_id -> stores": (~bills.store_id.isin(stores.store_id)).sum(),
        "stock_snapshot.sku_id -> items": (~snap.sku_id.isin(items.sku_id)).sum(),
        "stock_weekly.sku_id -> items": (~sw.sku_id.isin(items.sku_id)).sum(),
        "warehouse_inbound.sku_id -> items": (~inb.sku_id.isin(items.sku_id)).sum(),
        "flagship_protected.style_id -> items": (~t["flagship_protected"].style_id.isin(items.style_id)).sum(),
    }
    for k, n in fk.items():
        add("ok" if n == 0 else "error", k.split(".")[0], f"orphan keys {k}", n, "All keys resolve." if n == 0 else "Unmatched keys.", "none needed" if n == 0 else "rows ignored")

    # --- stock anomalies
    neg = snap[snap.on_hand < 0]
    add("error" if len(neg) else "ok", "stock_snapshot", "negative on_hand", len(neg),
        "; ".join(f"{r.location_id}/{r.sku_id} = {r.on_hand}" for r in neg.itertuples()) or "none",
        "contradictory position EXCLUDED from every action (never a donor or recipient)")
    load = snap[snap.location_id != d.warehouse_id].assign(tot=lambda x: x.on_hand + x.in_transit).groupby("location_id").tot.sum()
    cap = stores.set_index("store_id").stock_capacity_units
    over = (load - cap).dropna()
    over = over[over > 0]
    add("error" if len(over) else "ok", "stock_snapshot", "store above stock capacity", len(over),
        "; ".join(f"{k}: {int(load[k])} units vs capacity {int(cap[k])} (+{int(v)})" for k, v in over.items()) or "none",
        "no receipts allowed; excess is a pull-back / transfer candidate")
    onh = snap[snap.location_id != d.warehouse_id].merge(items[["sku_id", "cat2"]], on="sku_id").merge(stores[["store_id", "tier"]], left_on="location_id", right_on="store_id")
    cat = onh.groupby(["location_id", "tier", "cat2"]).apply(lambda g: (g.on_hand.clip(lower=0) + g.in_transit).sum(), include_groups=False).rename("units").reset_index()
    full = stores[["store_id", "tier"]].merge(t["min_display"], on="tier").rename(columns={"category": "cat2"})
    cat = full.merge(cat, left_on=["store_id", "cat2"], right_on=["location_id", "cat2"], how="left").fillna({"units": 0})
    below = cat[cat.units < cat.min_display_units]
    add("error" if len(below) else "ok", "stock_snapshot x min_display", "category below minimum display", len(below),
        "; ".join(f"{r.store_id} {r.cat2}: {int(r.units)} < {r.min_display_units}" for r in below.itertuples()) or "none",
        "display-minimum fills are generated first")
    nostore = snap[(snap.in_transit > 0) & snap.in_transit_arrives_on_dt.isna()]
    add("ok" if len(nostore) == 0 else "warn", "stock_snapshot", "in_transit>0 without arrival date", len(nostore), "none" if len(nostore) == 0 else "arrival date missing", "treated as arriving on plan date")
    it = snap[snap.in_transit > 0]
    add("info", "stock_snapshot", "units in transit (counted once, as arriving)", it.in_transit.sum(),
        f"{len(it)} positions, arrivals {sorted(set(it.in_transit_arrives_on))}", "counted as stock at destination from arrival date; never dispatched again; never donated")

    # --- availability / censoring
    last = sw[sw.week_ending_dt == sw.week_ending_dt.max()]
    ss = snap[snap.location_id != d.warehouse_id].set_index(["location_id", "sku_id"]).on_hand
    lw = last.set_index(["store_id", "sku_id"]).closing_on_hand
    j = pd.concat([lw, ss], axis=1, keys=["wk", "sn"]).dropna()
    mism = int((j.wk != j.sn).sum())
    add("ok" if mism <= 0.01 * max(len(j), 1) else "warn", "stock_weekly vs stock_snapshot", "last week closing_on_hand != snapshot on_hand", mism, f"{len(j)} shared positions", "snapshot is used as the stock position; weekly is used for availability only")
    part = sw.groupby(["store_id", "sku_id"]).days_in_stock.sum()
    add("info", "stock_weekly", "positions not in stock the full 12 weeks", (part < 84).sum(), f"{len(part)} store x SKU pairs; {(part == 0).sum()} never in stock",
        "velocity = units / in-stock days, not units / calendar days (stock-out censoring)")

    # --- sales
    add("info", "bill_lines", "return/void/negative quantity markers", (bl.qty <= 0).sum(), "quantity values: " + ", ".join(f"{k}:{v}" for k, v in bl.qty.value_counts().sort_index().items()),
        "LIMITATION: returns/cancellations are not identifiable in this data; velocity is gross sales")
    pk = items[items.is_packaging == 1].sku_id
    add("info", "items", "packaging SKUs (Rs 0, not products)", len(pk), ", ".join(pk), "excluded from velocity, stock, and actions")
    opened = stores[stores.opened_on_dt > d.as_of - pd.Timedelta(days=pol.velocity_window_days)]
    add("warn" if len(opened) else "ok", "stores", "store opened inside velocity window", len(opened),
        "; ".join(f"{r.store_id} opened {r.opened_on}" for r in opened.itertuples()) or "none", "window starts at opening date; peer-based estimate with low confidence")
    wh = snap[snap.location_id == d.warehouse_id]
    sold = bl.merge(bills, on="bill_id")
    sold = sold[sold.bill_date_dt > d.as_of - pd.Timedelta(days=90)].sku_id.unique()
    missing = set(sold) - set(wh.sku_id) - set(pk)
    add("warn" if missing else "ok", "stock_snapshot", "SKUs sold in last 90 days with no warehouse stock", len(missing), f"{len(wh)} SKUs at warehouse, {int(wh.on_hand.sum())} units", "supply is scarce: water-filling allocation; store-to-store sources considered")
    add("info", "warehouse_inbound", "inbound statuses", len(inb), "status values: " + ", ".join(f"{k}:{v}" for k, v in inb.status.value_counts().items()) + f"; {int(inb.qty.sum())} units, {inb.expected_date.min()} to {inb.expected_date.max()}",
        "LIMITATION: only 'Planned' - treated as scheduled supply usable on/after expected date, shown as scheduled")
    add("info", "lead_times", "route sentences parsed", len(t["lead_times"]), f"warehouse->store {d.lead_wh}; store->store {d.lead_s2s}", "structured lead-time rules")
    add("info", "min_display", "category mapping", len(d.cat_map), "; ".join(f"{k}->{v}" for k, v in d.cat_map.items() if k != v), "item categories summed into min_display categories")
    add("info", "flagship_protected", "protected styles", len(t["flagship_protected"]), "S++ stores: " + ", ".join(sorted(t["flagship_protected"].store_id.unique())), "protected styles never leave their S++ store")
    return F


def run_audit(d: Data) -> dict:
    return {"as_of": str(d.as_of.date()), "source_hash": d.source_hash, "tables": profile_tables(d), "findings": findings(d)}
