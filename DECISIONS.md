# NODAL — Decision log

| # | Decision | Reason |
|---|---|---|
| 1 | Brief B = Stock Mover (PDF is byte-identical to `briefs/B_Stock_Mover_brief.pdf`). Brief A (Store Coach) is out of scope. | Original PDF is authoritative. |
| 2 | Python monolith: pandas engine + FastAPI + static HTML/JS UI, no database, no build step. | 260k bill lines, auditable deterministic allocator; one command to run and explain. |
| 3 | No LLM in the decision path; explanations are templated from a structured evidence object. | Brief: organiser spot-checks numbers; quantities must be traceable. |
| 4 | Velocity = units / **in-stock days** (weekly `days_in_stock` + inference from oldest-unit age); thin evidence shrunk to same-SKU peer rate. | Zero sales ≠ slow product; stock-outs censor demand. |
| 5 | Festival uplift derived from last year's same-offset window (calendar + sales), applied per state. | No invented multipliers; Diwali is 8 Nov 2026, inside the horizon. |
| 6 | Pull-back = take stock out of an over-capacity / ageing position, preferring a store that sells it, else back to WH-KOL. **Assumption** — brief does not define destination. | Labelled in UI, configurable in `policy.py`. |
| 7 | Thresholds not in the data (reorder trigger 14d, target cover 30d, ageing 180d) are ASSUMED, centralised in `policy.py` and shown in the UI; 30-day donor cover and ₹1,00,000 approval limit are read from `rules.csv`. | Brief fixes only these two. |
| 8 | Independent validator re-derives post-plan state from source tables; violating actions are excluded, not warned. | Brief: "we check that no rule is broken". |
| 9 | Contradictory data (negative stock) is excluded from all actions, not repaired. | Never silently produce a confident action from bad data. |
| 10 | Repo contains the application, docs, tests and the unmodified organiser data pack (builder's call); the personal operator kit is git-ignored. | Judges can clone and run with no extra setup; data belongs to Vedant Fashions. |
| 11 | v2: Qwen3 8B (local Ollama) is advisory only. Facts, candidates and rules come from deterministic code; the model's numbers and evidence references are machine-checked; alternatives are re-validated. | Keeps inventory safety deterministic and auditable. |
| 12 | Public app serves a precomputed run bundle (`published/latest.json.gz`) and stores decisions in PostgreSQL; AI reviews are computed on the operator machine. | Small hosts cannot run an 8B model; the operator Mac need not stay on during judging. |
| 13 | Judges use the app with no login. Actions are recorded with an anonymous per-browser id; admin endpoints (upload/run) require `NODAL_ADMIN_TOKEN` and are disabled otherwise. | Builder's decision (no sign-in). Roles and user accounts were not built. |
| 14 | Decisions are an append-only log; identical repeat approvals are not written; moves above the sign-off limit need explicit confirmation. | Audit trail and no duplicate approvals. |
| 15 | Pull-back is **confirmed by the builder**: stock goes to a store that sells it, otherwise back to the warehouse (WH-KOL). | Resolves the brief's undefined term; still a policy value (`pullback_destination`). Not confirmed by Manyavar. |
| 16 | The plan is shown as store shipments (one decision per destination store; pull-backs per source store), with one confirmation for any big (sign-off) items inside. Details stay one tap away. | Builder asked to reduce overwhelm; about 600 moves become about 22 decisions. |
| 17 | Plain-language shipment summaries are written by the local Qwen3 model from a deterministic draft, number-checked and cached by a hash of the facts; the standard draft is the fallback. | Simple wording without trusting the model with facts. |
| 18 | "Ask": general questions go to Sarvam AI with only public facts; anything mentioning a store, SKU or the plan is answered locally. Answers are cached; per-client and global rate limits apply. | Builder's rule: no company data to third parties. |
| 19 | CSV upload and recalculation work on the operator machine only (disabled on the public site). Uploaded files overlay the supplied dataset. | Builder's choice; the public host cannot run the ML stack and must not accept anonymous uploads. |
| 20 | Visual direction: frosted glass over drifting, blurred solid-colour light (no gradient fills), Outfit thin type with lining numerals, red reserved for critical. | Builder's request for a calmer, more aesthetic UI. |
| 21 | The Sarvam API key lives only in the environment or a git-ignored `.env`; tests ignore `.env`. The key was pasted in chat, so it should be rotated. | Secret hygiene. |
