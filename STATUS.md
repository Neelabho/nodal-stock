# NODAL — Status

**Brief:** Manyavar Stock Mover (Case study B). **Repo:** https://github.com/Neelabho/nodal-stock

## v5 (latest): Sarvam is the only AI
- All AI (move reviews, shipment summaries, shortage actions, plan answers, general questions) now uses Sarvam; the local model is off by default. Last full run: 56 of 60 reviews completed (4 rejected by the safety checks; run marked `partial`), 22 of 22 summaries, 19 of 19 shortage actions. Privacy: store/product facts are sent to Sarvam; `AI_REMOTE_FALLBACK=0` turns all AI off.
- **Add data verified at pipeline level:** a modified stock file layered over the supplied data changed the plan as expected (warehouse stock zeroed for one product, so its 1 Nov sends switched from stock-on-hand to planned deliveries; all 11 rules pass). The browser upload click-through itself has not been run.

## v4
- **Moves has two switchable sections:** *Planned moves* (what the plan does) and *Shortages* (demand the plan could not cover: 19 stores, 337 items, 1,565 units, ₹1.22 Cr of sales at risk). Each shortage store has one plain-English suggested business action written by the local AI (all 19 on the last run), number-checked and cached; standard wording is the fallback. Verified in the app: switch, cards, items sheet with causes and planned warehouse deliveries, back gesture.
- **Local-first AI with Sarvam fallback everywhere:** move reviews, shipment summaries, shortage actions and plan answers try the local model first and use Sarvam if it is unavailable, with the same safety checks (live check with the local model unreachable: Sarvam returned valid JSON in 0.5 s). **Privacy note:** in fallback, store/product facts are sent to Sarvam; `AI_REMOTE_FALLBACK=0` turns this off. **Verified on a full pipeline run with the local model unreachable:** Sarvam wrote 22 of 22 shipment summaries and 19 of 19 shortage actions and completed 8 of 10 move reviews; the other 2 were rejected by the safety checks and the run was honestly marked `partial`, not complete.
- Moves are explained in the same seven labelled rows everywhere (Move, Route, Arrives, Demand, Cover, Source, Why); the one-line explanation required by the brief is kept for the CSV.
- Tests: 97 pass (full suite).

## v3: what changed and what is verified
- **Shipments:** 600 moves are shown as 22 store shipments (one decision each) with plain-language summaries written by the local model (all 22 from the model, number-checked, cached; template fallback). Approve / reject / skip with big buttons, swipe, keyboard shortcuts, Undo, and one-tap routine approval. One confirmation covers any big items inside a shipment; every approve and undo is in the audit log. Verified in the browser pane (approve, undo, skip, big-item confirmation).
- **Ask:** Sarvam AI answers general questions with public facts only; anything about a store, SKU or the plan is answered locally. Cached; per-minute, per-day and global limits (429 + Retry-After). Verified live: fresh answer 1.4 s, repeat 0.01 s from cache, plan question answered locally without Sarvam. **The API key was pasted in chat: please rotate it.** It is only in a git-ignored `.env`; git history contains no copy.
- **Add data (local app only):** upload per-file CSVs with validation, overlay on the supplied dataset, recalculate with live stage progress. Tested with API-level tests; the UI screen renders, but I have not run a real recalculation from an uploaded file in the browser.
- **Map:** ranked next stops by demand with each stop's top SKU and numbered pins (verified).
- **Design:** glass over blurred solid-colour light, Outfit thin type with uniform lining numerals, centred layouts, red only for critical, animations, animated logo intro. Pull-back policy confirmed by you.
- **Platforms:** laptop, tablet and phone layouts verified in the Chromium engine; install prompt, iOS hint and back-gesture handling added. **Not tested on a real iPhone, Android phone, Safari or Firefox; install and offline are untested.**
- Tests: 87 pass (≈2.5 min). Two real bugs were caught by testing this round (a self-recursive sheet helper and an inbox-detection inversion) and fixed.

## v2: what is verified
| Area | Evidence |
|---|---|
| Qwen3 8B review (local Ollama) | Real daily run on the supplied data: 60 highest-priority moves reviewed by `qwen3:8b` (≈2 s each), 43 agree / 17 flagged, 0 failed. The grounding guard rejected several first answers (invented numbers, unreferenced evidence) and the retry produced valid ones. |
| AI safety guards | 14 tests: malformed JSON, timeouts, retries, Ollama down (review stays `pending`, plan unaffected), invented quantities rejected, invalid evidence refs rejected, invented/unsupported alternatives downgraded to FLAG, cache reuse. |
| Alternatives | Deterministic candidates from NODAL's own ledger, each re-validated by the independent validator (tests re-check every candidate and a forged one is blocked). On the real plan 83 moves had candidates, only 2 were better by calculation (+₹1,641 and +₹62) and **Qwen3 selected none**, so the published plan contains **no AI alternative**. |
| Persistence + audit | SQLite and **PostgreSQL 17**: migrations apply, append-only decisions, idempotent approvals, 8 concurrent identical approvals write exactly 1 row, audit events, run history. |
| Daily pipeline | Run end to end on the real data (≈2.5 min with 60 AI reviews): input hash, immutable snapshot, stage records, bundle publish, notification file. Tests: duplicate dataset skipped, missing files stop the run with a reason, interrupted run closed, watched-folder detection, timezone scheduling. |
| Public app | Run in a clean virtualenv with **only** the public requirements (no pandas/numpy/scikit-learn/scipy) against PostgreSQL 17: `/healthz`, status, a persisted approval, map and UI all responded. |
| Phone UI | Checked at 375 px in the browser pane: no horizontal overflow, 0 touch targets under 44 px, one-move review, approve persists server-side, detail sheet with AI section, original-vs-alternative comparison renders (verified with a clearly-labelled, unpublished synthetic alternative) and "Approve AI alternative" is recorded. |
| Tests | superseded: see v3 above. |

## Not done / not verified (be honest)
- **No public URL yet.** Render needs your account; `render.yaml`, `Dockerfile`, `/healthz`, `.env.example` and `docs/DEPLOYMENT.md` are ready. Docker Desktop is paused here, so the **image was not built**; the public dependency set was validated in a venv instead.
- **QR code not generated** (needs the final HTTPS URL): `python scripts/make_qr.py https://<url>`.
- **PWA install not verified.** Manifest, icons, service worker and iOS tags exist and have static tests, but the embedded browser refuses service-worker registration, so install / offline behaviour is untested. Verify on the iPhone over HTTPS after deploy.
- **No real iPhone test.** Phone checks used a 375 px viewport in the browser pane.
- **No user accounts or roles** (your decision: judges use the app without logging in). Decisions carry an anonymous per-browser id. Operator endpoints need `NODAL_ADMIN_TOKEN`.
- Dashboard upload is per-file (`PUT /api/admin/inbox/<table>.csv`), API-only; there is no upload screen. SFTP, cloud-storage and inventory-system connectors are not built (no Manyavar API is assumed).
- Outcomes table exists but nothing fills it yet (needs actual-movement and later-sales feeds); no model is retrained from decisions.
- The 8B model's reasoning is imperfect (it once applied the donor cover rule to a receiving store). It is advisory, guarded, and never changes a quantity or rule.
- Local dev database `data/nodal.db` contains my test approvals; delete the file to reset.

## Unchanged from v1
Forecast trust scorecard (4 of 7 pre-declared checks pass; see `docs/FORECAST_BACKTEST.json`), pull-back destination assumption, thresholds 14/30/180 days, Planned-inbound reliability assumption.

## Next single action
Deploy the Blueprint on Render, open `/healthz` on the public URL, generate the QR code, and test Add-to-Home-Screen on the iPhone.
