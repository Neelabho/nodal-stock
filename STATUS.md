# NODAL — Status

**Brief:** Manyavar Stock Mover (Case study B). **Repo:** https://github.com/Neelabho/nodal-stock

## Verified (run locally on the supplied data, 21 Nov 2026)
- Engine runs end to end in ~1.6 s: 875 moves (446 warehouse→store, 324 store→store, 105 pull-backs), 3,249 units, ₹2.52 crore at MRP; 33 moves need human approval (> ₹1,00,000).
- Independent validator: 11/11 hard-rule checks pass, 0 moves excluded.
- `pytest`: 14 passed (traceability of velocity to source rows, capacity, protected styles, donor cover, supply limits, lead times, determinism, tamper detection).
- UI exercised in a browser: Overview, Action register (filters, approve/reject, evidence drawer), Data quality, Rules & gaps, Method all render; no console errors.
- Planted problems found: KNP-03 520/250 units (pulled back to capacity), BLR-05 new store + Sherwani display breach, MZF-01 Mojari display breach, MYS-02 negative stock (excluded), Diwali 8 Nov uplift (×1.9–2.3 from last year's data), 80 low-confidence moves flagged.

## Not done / not verified
- No public deployment yet. Not tested on a phone (responsive CSS only, checked at desktop width).
- Festival index rests on one prior cycle; thresholds 14/30/180 days are assumptions.
- 419 store×SKU positions (~2,983 units) remain unmet after warehouse and donor supply; listed in "Rules & gaps".
- Pull-back destination (WH-KOL) is an assumption; scheduled dispatches depend on inbound marked "Planned".
- Warehouse inbound beyond the first wave uses the 30-day horizon heuristic.

## Next
Deploy; phone-width check; demo script (one WH move, one refused move, KNP-03, BLR-05, Diwali effect).
