# NODAL — Stock Mover (application)

Explainable, rule-safe daily stock movement plan for Manyavar's Stock Mover brief (Brief B).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                                   # http://127.0.0.1:8000
.venv/bin/python -m pytest -q              # 14 tests on the real data
cd backend && ../.venv/bin/python -m nodal.cli   # headless run -> data/actions.csv + data/run_manifest.json
```

Data: the organiser CSVs must be at `../manyavar-vch3-data/csv/` (relative to this folder), or set `NODAL_DATA_DIR`.
Missing or malformed files produce an actionable error in the UI. Source files are never modified.

## Workflow in the UI

Overview (what was found) → Action register (filter, sort, inspect evidence, approve/reject, export CSV) →
Data quality → Rules & gaps (validator results, refused moves, unmet demand) → Method (policy, assumptions, manifest).

## Architecture

```
CSV → loader (typing, schema checks, lead-time/min-display parsing) → audit
    → velocity (units ÷ in-stock days, peer shrinkage, festival index)
    → allocate (one ledger; ageing → display minimum → warehouse water-filling → surplus transfers → pull-backs → scheduled inbound)
    → validate (independent re-derivation of post-plan state; violating moves excluded)
    → engine (priority, one-line explanations, summary, run manifest) → FastAPI → static UI
```

Python / pandas / FastAPI, no database, no build step, no LLM in the decision path. Business parameters live in
`backend/nodal/policy.py`; values stated by the organiser (30-day donor cover, ₹1,00,000 approval limit, lead times,
minimum display, capacities, protected styles) are read from the CSVs at run time.

## Hard rules enforced (and re-checked by `validate.py`)

Capacity · minimum display by category · S++ protected styles · in-transit counted once and never re-dispatched ·
donor keeps ≥ 30 days cover (SKU and style) · lead times · each source unit allocated once · ₹1,00,000 approval flag ·
zero-sales stock only moved when slowness is proven · contradictory data excluded.

## Assumptions and limits

See `docs/INITIAL_ANALYSIS.md` §6 and the Method tab. Notably: "pull back" destination is undefined in the brief
(stores that sell the SKU first, else WH-KOL); thresholds 14 / 30 / 180 days are assumptions; returns are not
identifiable in the data; warehouse inbound is all "Planned".
