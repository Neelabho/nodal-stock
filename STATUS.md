# NODAL — Status

**Brief:** Manyavar Stock Mover (Case study B). **Repo:** https://github.com/Neelabho/nodal-stock

## Sign-off checklist (agreed with the builder)
| Criterion | State |
|---|---|
| Rules pass + tests green | **Done.** Validator 11/11 on the live run, 0 moves excluded. `pytest`: 21 passed (≈80 s). Mobile UI (375 px) checked in the browser pane: no horizontal scroll, 44 px touch targets, one-move review flow works; not tried on a physical iPhone. |
| Planted problems visibly handled | **Done in data, check in UI.** KNP-03 520/250 pulled back to 250; BLR-05 and MZF-01 display breaches filled first; MYS-02 negative stock excluded; protected styles refused. Each has a card on Overview that opens the matching moves. |
| Public URL works | **Not done.** Runs locally only (`./nodal-stock-mover/run.sh`). Needs a Python host. |
| Two-minute demo recorded | **Not done.** |

## Verified (this session, locally)
- End-to-end run ≈ 35 s (ML training + LP + greedy comparison): ~600 moves, ~2,850 units, ₹2.3 crore at MRP; 24 need human approval (> ₹1,00,000).
- UI checked in the browser pane: Overview with approval queue (approve works, queue refills), Action register, Hotspot map (markers, ranking, move lines), Rules & gaps, Data quality, Forecast model, Method. No console errors. Phone width (375 px): no horizontal scroll.
- Design: flat palette (solid colours, 1 px borders, no gradients or shadows), Rufina + Figtree, Lucide line icons (ISC, pinned 0.469.0). Palette is inspired by Manyavar's public site colours (maroon / cream / sand); no logos or assets copied.

## Forecast model — measured, not claimed (docs/FORECAST_BACKTEST.json)
Pure ML (Poisson gradient boosting); tuned on validation origins only; no store/SKU IDs as inputs.
- Beats the old trailing-rate method on Poisson deviance at every test origin, and on WAPE at 3 of 4.
- Bias 1.05 to 1.19 (target ±10%: **fails** at the Sep origin). Store×category WAPE 0.16, 0.17, 0.25, 0.29 (target ≤ 25% at 3 of 4: **fails** narrowly). Probability calibration ECE 0.02 (**passes**).
- Unseen styles: better than the peer baseline. Unseen stores: roughly equal (worse at one origin, better at the other) — **fails** the "not worse" check.
- Per store×SKU weekly WAPE ≈ 0.55–0.62 is the Poisson noise floor; "90% accuracy" is not attainable at that level and is not claimed.
- Festival-window accuracy rests on one Diwali with training signal (stress test under-forecasts): least validated part.

## Not working / unverified
- No public deployment; no demo video. Map tiles need internet. Store map positions are approximate city centres.
- LP optimiser adds ≈ 0.2–0.9% expected revenue over greedy: real but small.
- ~330 store×SKU positions (~1,500 units) remain unmet after warehouse and donor supply; listed in Rules & gaps.
- "Pull back" destination (WH-KOL) is an assumption; scheduled dispatches depend on inbound marked "Planned".

## Next single action
Decide deployment host, then record the demo.
