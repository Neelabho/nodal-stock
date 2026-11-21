# NODAL — Initial Analysis (Brief B: Stock Mover)

Sources read: original PDF `manyavar-vch3-data/2b6d36a5-71ed-4792-beac-68ef31bb2822.pdf` (byte-identical to `briefs/B_Stock_Mover_brief.pdf`), the data pack README, `data_dictionary.csv`, all 17 CSVs, `CLAUDE.md`, the operator skill, the kit docs. All numbers below were computed from the supplied CSVs (audit scripts: `backend/nodal/audit.py`, re-runnable; output in `docs/DATA_AUDIT.json` once generated).

## 1. Brief B — the exact problem

**Brief B = Case study B, "Stock Mover" (Vedant Fashions / Manyavar).** Brief A (Store Coach) is a different problem on the same data and is out of scope.

Official statement: *"Every day, find stock that's in the wrong place. Produce a ready-to-approve list: what to send from the warehouse, what to move between stores, and what to pull back."*

The brief says the system **must handle four things**: (1) warehouse → store when there isn't enough for everyone, (2) under-stocked stores (would sell more if they held more), (3) store → store ("from where stock sits to where it sells"), (4) ageing stock ("move it to where it sells before it's too late").

**Stated rules (judging criteria):**
1. Every store keeps a minimum display per category (`min_display`); never exceed store capacity.
2. S++ flagships never give away their top-20 styles (`flagship_protected`).
3. In-transit stock counts as arriving; never send it twice.
4. A store→store move must not leave the giving store under **30 days of cover** (rules tab: "for that style").
5. Ignore transport cost; lead times still matter. Explain every move in one line.
6. Additional rule from the `rules` CSV (not in the PDF text): any single move worth **> ₹1,00,000 at MRP needs human approval**; the company replenishes every Monday but the system "can recommend any day".

**How it is judged:** the organiser knows where the stock problems are hidden and checks whether we found them; spot-checks numbers; checks no rule is broken; checks it "runs on its own". A merchandiser should be able to approve the list in ten minutes. "A product with zero sales isn't always a slow product. Check before you trust."

**Key definitions:** velocity = how fast each style *and size* sells in each store; cover = stock ÷ daily velocity.

### Discrepancies between kit and source (source wins)
| Kit says | Source says | Resolution |
|---|---|---|
| Kit docs (`PROJECT_BRIEF.md`, `BUILD_PLAN.md`, `DECISIONS.md`, `STATUS.md`, `README_PROJECT.md`) are at repo root | They are blank templates in `templates/` | Working copies created at root; templates untouched. |
| Kit: "dataset not included; locate organiser workbook" | Dataset is in `manyavar-vch3-data/csv/` (xlsx is *not* present; CSVs only) | Use CSVs. |
| Kit: pull-back destination is unknown/ambiguous | PDF lists "pull back" in the intro but defines no destination; "Ageing stock: move it to where it sells" | Pull-back = take stock out of a store where it is over-stocked/ageing; **first choice destination is a store that sells it**, fallback is WH-KOL. Labelled as an assumption (see §6). |
| Kit: no mention of the ₹1,00,000 approval rule | `rules.csv` row "Approval" | Implemented as an approval flag. |
| Kit CLAUDE.md: stop at an approval gate before coding | Builder's latest instruction: proceed without waiting | Builder's instruction wins; recorded in DECISIONS.md. |

## 2. Business workflow

Daily (as-of 31 Oct 2026 close): merchandiser opens NODAL → sees data-quality findings → engine runs on its own → ranked action register (warehouse→store, store→store, pull-back/ageing) → inspects any move (velocity, cover before/after, lead time, rules checked, why this source) → approves/rejects → exports CSV. Nothing is dispatched automatically; every action is "proposed" and >₹1L ones are "needs human approval".

## 3. Data inventory (Stock Mover files)

| File | Rows | Role in Brief B |
|---|---|---|
| `stores` | 20 | tier, zone/state/city, **stock_capacity_units**, opened_on (BLR-05 opened 2026-10-10) |
| `items` | 385 (75 styles; 382 products + 3 packaging) | category, size, MRP, launch_date, is_core_style, is_packaging |
| `bills` / `bill_lines` | 90,805 / 259,772 | sales → velocity. 2024-11-01…2026-10-31 |
| `stock_snapshot` | 3,547 | on_hand, in_transit (+arrival date), oldest_stock_received_on; 20 stores + WH-KOL |
| `stock_weekly` | 61,416 | 12 weeks (15 Aug–31 Oct), closing_on_hand, **days_in_stock** → availability / stock-out censoring |
| `warehouse_inbound` | 768 | 19,526 units due 1 Nov–16 Dec 2026, all status "Planned" |
| `calendar` | 2,373 | festivals by state + wedding dates (Nov 2024–Dec 2026) |
| `rules`, `min_display`, `lead_times`, `flagship_protected` | 8, 24, 6, 80 | policy inputs, read at runtime |
Not used for Brief B: `area_managers`, `salespeople`, `roster_daily`, `footfall_daily` (Brief A material; footfall may be a later context signal).

## 4. Data audit — findings

**Clean:** no duplicate keys in any table; all FKs resolve (bill_lines→bills/items, snapshot/inbound/stock_weekly→items, bills→stores); no sale before launch date; no future-dated bills; snapshot has a single as-of date; last stock_weekly week matches snapshot on-hand for 3,405 of 3,407 shared store×SKU pairs.

**Anomalies (each handled explicitly, none "fixed" in the source):**
| # | Finding | Handling |
|---|---|---|
| 1 | **KNP-03 holds 520 units vs capacity 250** (270 over; 319 Sherwani vs display min 5) | Planted problem. No receipts allowed; pull-back/transfer of excess. |
| 2 | **MYS-02 / KUR-02-M on_hand = −4** | Contradictory; position excluded from all actions and shown in DQ report. |
| 3 | **BLR-05 is a brand-new store** (opened 10 Oct 2026): 214 units on hand, 63 units sold (cover ≈ 309 days at store level); Sherwani 2 vs min 5 | Cold-start velocity (peer-based, low confidence); display-min fill. |
| 4 | **MZF-01 Mojari 5 vs min 8**; BLR-05 Sherwani 2 vs min 5 (only two categories below min display) | Display-min fills first. |
| 5 | **1,604 of 3,378 stocked store×SKU positions (2,020 units) had zero sales in 90 days**; ~90% of them were in stock all 12 weeks (true slow), median oldest-unit age 303 days; 1,244 positions have oldest unit >180 days | Ageing stock. Zero velocity is only trusted when availability is proven. |
| 6 | **28 store×SKU rows have on_hand=0 yet sold in last 90 days**; 492 sold pairs (2,510 units) have no snapshot row at all | Stock-outs: zero stock ≠ zero demand → under-stocked store signals. |
| 7 | 2,506 of 5,235 weekly pairs were not in stock the full last 4 weeks; 90 of 702 fast sellers had <70 of 84 days in stock | Velocity must be divided by **days in stock**, not calendar days (censored demand). |
| 8 | **Strong festival seasonality.** Diwali 2026 = 8 Nov (all 3 states), Chhath 15 Nov (Bihar). Last year's analogous window shows **~2.1×–2.5×** the trailing-84-day rate (state-level) | Seasonal index derived from last year's same-offset festival window (no invented multipliers). |
| 9 | Warehouse holds 15,407 units across 140 SKUs; 147 SKUs sold in last 90 days are **not at the warehouse**; 19 WH SKUs have <30 days WH cover | Supply is scarce for many SKUs → fair allocation matters. |
| 10 | `bill_lines` has qty ∈ {1,2,3} only, no negatives, no void/return/exchange markers | Returns not identifiable → disclosed limitation. |
| 11 | `launch_date` blank for the 3 packaging SKUs; `oldest_stock_received_on` blank on 28 zero-stock rows; `calendar.festival` blank on 2,329 rows | Expected blanks, per README. Packaging excluded everywhere. |
| 12 | `min_display` category "Accessories (Safa/Mala/Stole)" vs three item categories | Mapped (documented gotcha #9). |
| 13 | `lead_times.route` is a sentence | Parsed into structured rules (WH→zone; store→store same city / same zone / different zone). |
| 14 | `warehouse_inbound` statuses: only "Planned" | Cannot distinguish confirmed vs tentative → treated as scheduled supply usable only on/after expected date, shown separately from on-hand. |
| 15 | Top-20 protected styles exist only for the 4 S++ stores (LKO-01, PAT-01, BLR-01, BLR-02); 391 protected store×SKU positions hold 2,801 units | Excluded from donors. |

**Limitations:** only 12 weeks of availability evidence; no returns data; no cost/margin use (transport ignored by brief); wedding-date effect is captured only implicitly through last-year windows; inbound "Planned" status ambiguous; "pull back" destination undefined.

## 5. Architecture (solo, hackathon-sized)

**Python monolith: pandas engine + FastAPI + a no-build static web UI.**
- *Why Python/pandas:* 260k bill lines and a deterministic, auditable allocator are tabular work; no database needed (read-only CSVs, in-memory run takes seconds).
- *Why FastAPI:* thin JSON API + serves the static UI from one process → one command to run, one deploy target.
- *Why vanilla HTML/JS (no React/Node build):* fewer moving parts for a solo developer to explain; zero build step; works offline for demos.
- *No LLM in the decision path.* Every quantity is deterministic code; explanations are templated from a structured evidence object.
- *Persistence:* none server-side. Approve/reject stored in browser localStorage and included in the CSV export. Each run emits a downloadable JSON run manifest (inputs hash, policy snapshot, parameters, engine version).

```
CSV (read-only) → loader+typing → audit/DQ report ┐
                                                  ├→ velocity (in-stock-days, shrinkage, seasonality)
                                                  ├→ position ledger (on_hand, in_transit, planned in/out)
                                                  ├→ allocators: ageing→store, min-display, WH→store (water-fill), surplus store→store, pull-back
                                                  ├→ independent validator (blocks any violating action)
                                                  └→ explain (1-line + evidence) → API → UI / CSV / manifest
```

## 6. Core algorithm and assumptions

Plan date = day after snapshot (dispatch 1 Nov 2026); arrival = dispatch + lead time (WH→store by destination zone; store→store by same city/zone/other). Policy values come from the CSVs; assumptions live in one `policy.py` and are shown in the UI.

1. **Velocity** (units/day, per store × SKU): units sold in last 84 days ÷ *days in stock* (from `stock_weekly`), not ÷84. When evidence is thin (<42 in-stock days) shrink toward the peer rate (same SKU, same tier, in-stock-adjusted) with k=14 days. Confidence = high/medium/low from in-stock days and units. New store: window starts at opening. *Assumption:* 84-day window = the length of availability data.
2. **Seasonality index:** per state, ratio of (units/day in the horizon window one year earlier at the same offset from the same festival) ÷ (trailing 84-day units/day one year earlier). Applied to velocity over the horizon; clipped and flagged if sample is thin. Derived entirely from sales + calendar.
3. **Projection:** stock at arrival = on_hand + in_transit (counted once, as arriving 1–2 Nov) − velocity × seasonality × lead time. Cover = stock ÷ (velocity × seasonality).
4. **Need:** position is under-stocked if cover at arrival < 14 days; order up to 30 days of cover at arrival. *(Assumptions: 14/30; the brief only fixes the 30-day donor floor.)* Also: min-display gaps by category (on_hand + in_transit vs min).
5. **Surplus:** donor units = on_hand − 30 days × velocity × seasonality (SKU level and style level), minus category display floor; zero for protected styles at S++ stores; in-transit never donated; negative-stock positions excluded.
6. **Order of allocation** (single ledger, every unit allocated once): (a) ageing store stock → stores that sell the SKU; (b) category min-display fills; (c) warehouse → store by **water-filling** (give next unit to the position with the lowest resulting cover, so scarce stock equalises cover and the most-urgent stores are served first), capped by store capacity room; (d) surplus store→store for remaining deficits, ranked by urgency, nearest donor (shortest lead time) first; (e) pull-backs: over-capacity stores and ageing stock nobody sells (to WH-KOL). Warehouse inbound is allocated as later-dated scheduled dispatches (not available before its expected date).
7. **Ageing:** oldest unit ≥ 180 days AND (proven zero velocity — in stock ≥ ~all 84 days with zero sales — or cover ≥ 180 days). Core styles are less at risk than limited-run fashion styles, shown as evidence, not a hidden weight. *(Assumption: thresholds.)*
8. **Zero-sales check:** zero sales is classified (a) proven slow (in stock the whole window), (b) censored (stocked out part of the window → use in-stock rate), (c) no evidence (not stocked / new) → peer estimate, labelled low-confidence.
9. **Validator (independent code path):** capacity, display floors, protected styles, no double allocation, WH and donor supply, donor 30-day cover, lead-time arrival, approval threshold, explanation present. Violations are *excluded*, never marked "approvable".

## 7. Planned structure

```
nodal-stock-mover/
  README.md  requirements.txt  run.sh  .gitignore
  backend/nodal/ {policy, loader, audit, velocity, ledger, allocate, validate, explain, api}.py
  frontend/ {index.html, app.js, styles.css}
  data/            # derived outputs only (git-ignored); sources stay in manyavar-vch3-data/
  docs/ {INITIAL_ANALYSIS.md, ARCHITECTURE.md (if needed), DATA_AUDIT.json}
  tests/           # pytest: invariants on real data + unit tests
```

## 8. Roadmap

1. Scaffold + analysis + repo (this doc) → push.  2. Loader + audit/DQ.  3. Velocity + seasonality.  4. Ledger + allocators.  5. Validator + tests (invariants on real run).  6. API + UI (register, detail drawer, DQ tab, export, approvals).  7. End-to-end run in browser, fix issues.  8. STATUS/README/DECISIONS, commit milestones.  9. Demo prep (a traceable WH move, a blocked move, KNP-03 over-capacity, BLR-05 cold start, Diwali effect).

## 9. Risks / open questions

- **Pull-back meaning** undefined (assumption in §1/§6; surfaced in UI).  
- Thresholds 14/30/180 days are assumptions — exposed as parameters, not claimed as Manyavar policy.
- Seasonality from one prior festival cycle (2025) — thin evidence; shown with confidence.
- "Planned" inbound might never arrive → scheduled actions are labelled.
- Many positions have zero evidence (peer fallback is an estimate).
- Judges' hidden planted problems may include ones I haven't spotted; the DQ/anomaly report is systematic rather than hand-picked to reduce that risk.
- No public deployment target chosen yet (needs Python host; low priority vs. correctness).
