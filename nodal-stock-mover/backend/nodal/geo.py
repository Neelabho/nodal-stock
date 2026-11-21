"""Approximate city-centre coordinates for the map. The supplied data has cities but no store coordinates,
so stores are placed around their city centre (deterministic offsets) - positions inside a city are illustrative."""
CITY_LATLON = {
    "Lucknow": (26.8467, 80.9462), "Kanpur": (26.4499, 80.3319), "Patna": (25.5941, 85.1376), "Muzaffarpur": (26.1209, 85.3647),
    "Bengaluru": (12.9716, 77.5946), "Mysuru": (12.2958, 76.6394), "Kolkata": (22.5726, 88.3639),
}
WAREHOUSE_CITY = "Kolkata"   # WH-KOL


def place(stores) -> dict:
    """store_id -> (lat, lon); unknown cities are omitted (never invented)."""
    out, seen = {}, {}
    ring = [(0.0, 0.0), (0.045, 0.03), (-0.04, 0.04), (0.03, -0.05), (-0.05, -0.03), (0.06, 0.0)]
    for r in stores.sort_values("store_id").itertuples():
        if r.city not in CITY_LATLON:
            continue
        k = seen.get(r.city, 0); seen[r.city] = k + 1
        la, lo = CITY_LATLON[r.city]; dy, dx = ring[k % len(ring)]
        out[r.store_id] = (la + dy, lo + dx)
    return out
