# NODAL — Stock Mover

**Hackathon brief:** Manyavar / Vedant Fashions, Case study B — *Stock Mover* ("Right stock. Right store. Right size.").

NODAL finds stock that is in the wrong place and produces a ready-to-approve daily action list:
**warehouse → store**, **store → store**, and **pull-backs** (ageing / over-capacity stock). Every move is
deterministic, rule-checked by an independent validator, and explained in one line with the evidence behind it.
No LLM is involved in any quantity.

## Run it

Requires Python 3.11+ (3.12 in the public image). The supplied Manyavar data pack is included unmodified under `manyavar-vch3-data/`
(the app reads `manyavar-vch3-data/csv/`; override with `NODAL_DATA_DIR`). Data and briefs belong to Vedant Fashions Limited.

```bash
cd nodal-stock-mover
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                      # web app on http://127.0.0.1:8000
.venv/bin/python -m pytest -q # tests on the real data
cd backend && ../.venv/bin/python -m nodal.cli   # headless: writes data/actions.csv + run manifest
```

## v2: AI review, daily automation, public phone app

- **Qwen3 8B (local Ollama)** reviews the highest-priority moves and compares validated alternatives. It is advisory: its numbers and evidence are machine-checked and nothing it says changes a quantity or rule.
- **Daily pipeline** (`python -m nodal.cli daily | schedule | watch`): ingest, hash, snapshot, forecast, allocate, validate, review, publish.
- **Server-side approvals** with an append-only audit trail (SQLite locally, PostgreSQL in production).
- **Installable phone app** (PWA) that judges open from a QR code, no sign-in. Approving a plan never dispatches stock.
- Architecture, operations and deployment: `nodal-stock-mover/docs/DEPLOYMENT.md`. Honest status: `STATUS.md`.

## What to look at

- `nodal-stock-mover/docs/INITIAL_ANALYSIS.md` — Brief B requirements, data audit, architecture, algorithm, risks.
- `DECISIONS.md` — significant decisions and assumptions. `STATUS.md` — what is verified and what is not.
- `nodal-stock-mover/backend/nodal/` — engine (`velocity`, `allocate`, `validate`, `engine`), API, policy layer.

See `nodal-stock-mover/README.md` for architecture and method.
