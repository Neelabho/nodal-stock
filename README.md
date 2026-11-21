# NODAL — Stock Mover

**Hackathon brief:** Manyavar / Vedant Fashions, Case study B — *Stock Mover* ("Right stock. Right store. Right size.").

NODAL finds stock that is in the wrong place and produces a ready-to-approve daily action list:
**warehouse → store**, **store → store**, and **pull-backs** (ageing / over-capacity stock). Every move is
deterministic, rule-checked by an independent validator, and explained in one line with the evidence behind it.
No LLM is involved in any quantity.

## Run it

Requires Python 3.11+. Place the organiser data pack so that the CSVs are at `manyavar-vch3-data/csv/`
(or set `NODAL_DATA_DIR`). The pack is not redistributed in this repo.

```bash
cd nodal-stock-mover
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                      # web app on http://127.0.0.1:8000
.venv/bin/python -m pytest -q # tests on the real data
cd backend && ../.venv/bin/python -m nodal.cli   # headless: writes data/actions.csv + run manifest
```

## What to look at

- `nodal-stock-mover/docs/INITIAL_ANALYSIS.md` — Brief B requirements, data audit, architecture, algorithm, risks.
- `DECISIONS.md` — significant decisions and assumptions. `STATUS.md` — what is verified and what is not.
- `nodal-stock-mover/backend/nodal/` — engine (`velocity`, `allocate`, `validate`, `engine`), API, policy layer.

See `nodal-stock-mover/README.md` for architecture and method.
