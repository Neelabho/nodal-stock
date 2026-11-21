# NODAL v2: architecture, operations and deployment

## Logical architecture

```
OPERATOR MACHINE (has Ollama + Qwen3 8B)                         PUBLIC (Render: web service + PostgreSQL)
------------------------------------------------                 ---------------------------------------------
inbox / upload  -> daily pipeline (cron/launchd)                 FastAPI + static PWA (NODAL_MODE=published)
  validate, hash, snapshot                                         serves published/latest.json.gz
  forecast -> allocate -> 11-rule validator                        stores decisions + audit in PostgreSQL
  alternative candidates (deterministic, re-validated)             no ML libraries, no Ollama, no company CSVs needed
  Qwen3 review (local, advisory)                                   /healthz for health checks
  publish: published/latest.json.gz  --git push-->  redeploy
```

Why this split: a free or small web host cannot run Qwen3 8B, and the operator's Mac need not stay on for judges. The public app never calls
Ollama; it serves the reviews computed on the operator machine. If Ollama is down the deterministic plan still completes and the run is marked
`ai_status = pending` (never reported as fully reviewed).

## Safety model (unchanged and tested)
- Deterministic Python code owns every quantity and rule. The independent validator re-checks all 11 hard rules; a failing move is excluded.
- Qwen3 only sees facts NODAL computed. Every number it writes must appear in those facts; it must cite real fact keys; malformed, ungrounded or
  unreferenced answers are retried once and then discarded. An `ALTERNATIVE` must name one of the pre-generated candidates, and the candidate's
  NODAL-calculated expected-revenue change must be non-negative, otherwise the outcome is downgraded to `FLAG`.
- Every candidate alternative is re-validated by the same validator inside the otherwise-unchanged plan before it can be shown or approved.
- Approval records approval of the plan. Nothing dispatches stock. Moves above the sign-off limit (read from `rules.csv`) need explicit confirmation.
- Model-stated confidence is shown as such and is not a calibrated probability.

## Data model (SQLite locally, PostgreSQL in production; `DATABASE_URL`)
`runs`, `moves`, `ai_reviews` (state: pending, processing, completed, failed, skipped), `decisions` (append-only), `audit_events`, `outcomes`
(foundation for comparing recommendation, alternative, human choice and observed result), `schema_migrations`. Schema changes are new numbered
entries in `db.MIGRATIONS`. Verified on SQLite and PostgreSQL 17.

## Daily operations
| What | How |
|---|---|
| Run once now | `cd backend && ../.venv/bin/python -m nodal.cli daily [--force] [--no-ai]` |
| Run every day | `python -m nodal.cli schedule` (uses `NODAL_DAILY_TIME`, `NODAL_TZ`, default 06:00 Asia/Kolkata) or the launchd template in `deploy/` |
| Watch a folder | `python -m nodal.cli watch` (new complete dataset in `NODAL_INBOX` triggers a run) |
| Upload a dataset | `PUT /api/admin/inbox/<table>.csv` with header `X-Admin-Token` (set `NODAL_ADMIN_TOKEN`; disabled otherwise) |
| Publish | `scripts/publish.sh` (runs the pipeline, commits `published/latest.json.gz`, pushes) |
Idempotency: the input hash of the datasets is recorded; identical input is skipped and audited, never republished. Runs stuck in `running` for over
3 hours are closed as `failed: interrupted`. Previous snapshots (`data/snapshots/<run_id>`) and bundles are never overwritten. Not built: SFTP,
cloud-storage and inventory-system connectors (the ingestion layer is modular so they can be added; no Manyavar API is assumed).

## Deploy the public app (Render)
1. Run the pipeline locally and commit `published/latest.json.gz` (`scripts/publish.sh`).
2. Render dashboard -> New -> Blueprint -> pick this repository. `render.yaml` creates the web service (Docker) and the PostgreSQL database.
3. Environment: `DATABASE_URL` is wired automatically; `NODAL_ADMIN_TOKEN` is generated; `NODAL_MODE=published`.
4. Open `https://<service>.onrender.com/healthz` (expect `{"ok": true}`), then `/`.
5. `python scripts/make_qr.py https://<service>.onrender.com` writes `docs/qr.png`. A judge scans it with the iPhone camera, opens the page, and can use
   Share -> Add to Home Screen.
Notes (from Render's published docs, not independently verified): free web services sleep after about 15 minutes idle and take about a minute to wake;
free PostgreSQL expires after 30 days. For a live judging session use an always-on paid plan or open the URL a few minutes beforehand.

## Qwen3 hosting options
- Default (implemented): reviews are computed on the operator machine and published with each run. No inference server is exposed.
- Optional live inference later: a dedicated GPU host running Ollama behind an authenticated reverse proxy; set `OLLAMA_BASE_URL` and `OLLAMA_API_KEY`.
  Never expose Ollama's port directly.

## Local development
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh                                  # serves the latest published run if present, otherwise computes the plan
export OLLAMA_BASE_URL=http://127.0.0.1:11434 OLLAMA_MODEL=qwen3:8b   # defaults
```
Copy `.env.example` for all settings. Run all tests: `.venv/bin/python -m pytest -q`. Run the persistence tests on PostgreSQL:
`NODAL_TEST_DATABASE_URL=postgresql://user@host/db pytest tests/test_persistence_api.py`.

## Unresolved business assumptions (unchanged, still configurable)
Pull-back destination (WH-KOL unless a store sells the SKU), ageing thresholds (180 days), 14/30-day cover targets, and treating warehouse inbound
marked "Planned" as reliable. See `backend/nodal/policy.py` and `DECISIONS.md`.

## v3 additions
- **Shipments:** the pipeline groups moves into store shipments and writes plain-language summaries with the local model (cached; template fallback). They are published in the bundle, so the public app shows them with no AI at request time.
- **Ask:** `POST /api/ask`. General questions use Sarvam (`SARVAM_API_KEY`, `SARVAM_MODEL`); the router sends only questions with no store, SKU or plan words, plus a fixed block of public rules. Plan questions are answered locally from shipment facts. Answers are cached in `qa_cache`; limits `QA_PER_MINUTE`, `QA_PER_DAY`, `QA_GLOBAL_PER_DAY` return HTTP 429 with `Retry-After`. On Render set `SARVAM_API_KEY` in the service's environment settings yourself.
- **Add data:** local app only. `PUT /api/inbox/<table>` validates and stores a CSV; uploaded files overlay the supplied dataset; `POST /api/pipeline/run` recalculates in the background with stage progress. Disabled when `NODAL_MODE=published`.

## Platforms
NODAL is one responsive web app (a PWA), not three apps. It runs in the browser on iPhone/iPad (Safari), Android (Chrome) and laptops (Chrome, Edge, Safari, Firefox).

| Form factor | Layout | Install |
|---|---|---|
| Phone (up to 700 px) | bottom tab bar, one shipment at a time, swipe, sheets | iPhone: Share, then Add to Home Screen (the app shows this hint). Android: browser install prompt or the in-app "Install app" button |
| Tablet / small laptop (701-1000 px) | compact icon rail, centred cards | same as above |
| Laptop / desktop | floating glass sidebar, keyboard shortcuts (right arrow approve, left arrow reject, S skip) | Chrome / Edge show an install icon; the in-app "Install app" button appears when the browser offers it |

Details: the Android/iOS back gesture closes a sheet instead of leaving the app; screen-height units use `dvh` so mobile browser bars do not clip the layout; safe-area insets cover notches and landscape; the glass effect carries the `-webkit-` prefix for Safari; reduced-motion settings disable the animations and the intro.

**Tested here:** Chromium engine at laptop (1280 px), tablet (820 px) and phone (about 360 px, Android user agent) sizes: layouts, sheets, back gesture, keyboard shortcuts, no horizontal overflow.
**Not tested:** a real iPhone or Android phone, Safari, Firefox, installing the app, service-worker/offline behaviour (the embedded test browser blocks service workers). Check these on real devices over the HTTPS URL after deployment.
