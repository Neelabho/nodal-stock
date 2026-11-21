"""Qwen3 review of NODAL moves.

The model is advisory. It only sees facts computed by NODAL; every number in its text must appear in those facts; an
ALTERNATIVE must name a deterministic candidate that was already re-validated and shows a non-negative calculated effect.
Its verdict is stored separately from (and never changes) the deterministic validation result.
"""
from __future__ import annotations
import hashlib, json, logging, os, re
from .ollama import OllamaClient, OllamaError
from .schemas import AIReview, REVIEW_SCHEMA
from .. import db

log = logging.getLogger("nodal.ai")
PROMPT_VERSION = "v1"

SYSTEM = ("You review stock-movement proposals for a menswear retailer's inventory manager. Use ONLY the JSON facts and candidates you are given. "
          "Never invent or estimate numbers, stock levels, forecasts or rules; if you quote a number it must appear in the facts exactly. "
          "The proposal has already passed all hard business rules; judge whether it is sensible and say so plainly. "
          "Outcomes: CONFIRM (reasonable), FLAG (a concern or inconsistency), ALTERNATIVE (one provided candidate is better; set preferred_candidate to its id), "
          "INSUFFICIENT_EVIDENCE (facts are not enough). If there are no candidates you may not answer ALTERNATIVE. Keep every field short. "
          "The minimum-cover rule applies only to a store that GIVES stock away, never to a receiving store or a warehouse dispatch. "
          "evidence_refs is mandatory: list 2-5 exact keys from `facts` that justify your outcome.")


def build_facts(a: dict, evidence: dict, policy: dict) -> dict:
    rc, dn = evidence.get("recipient") or {}, evidence.get("donor") or {}
    f = {"move_type": a["type_label"], "reason": a["reason_label"], "units": a["qty"], "sku": a["sku_id"], "style": a["style_name"], "category": a["category"], "size": a["size"],
         "from": a["source"], "to": a["destination"], "dispatch_date": a["dispatch_date"], "arrival_date": a["arrival_date"], "lead_days": a["lead_days"],
         "value_mrp_rupees": a["value_mrp"], "needs_human_signoff": bool(a["requires_approval"]), "validator": "passed all hard rules",
         "rule_signoff_threshold_rupees": policy.get("approval_value_mrp"), "flags": a.get("flags", [])}
    if a["type"] != "WH_TO_STORE":                                  # the 30-day cover floor binds only the store that GIVES stock
        f["rule_giving_store_min_cover_days"] = policy.get("donor_min_cover_days")
    if rc and a["type"] != "PULL_BACK":
        f.update({"destination_forecast_units_per_day": rc.get("velocity_used"), "destination_trailing_units_per_day": rc.get("velocity_trailing"), "destination_cover_days_before": a.get("cover_before_days"),
                  "destination_cover_days_after": a.get("cover_after_days"), "destination_on_hand": rc.get("on_hand"), "destination_units_sold_84d": rc.get("units_84d"),
                  "destination_capacity_room": rc.get("capacity_room_before"), "destination_confidence": rc.get("confidence")})
    if dn:
        f.update({"source_on_hand_before": dn.get("on_hand_before"), "source_on_hand_after": dn.get("on_hand_after"), "source_cover_days_after": dn.get("sku_cover_after_days"),
                  "source_units_sold_84d": dn.get("units_84d"), "source_oldest_unit_age_days": dn.get("oldest_unit_age_days"), "source_is_ageing": dn.get("is_ageing")})
    sup = evidence.get("supply") or {}
    if sup:
        f.update({"warehouse_available_before": sup.get("warehouse_available_before"), "warehouse_available_after": sup.get("warehouse_available_after"), "warehouse_short": sup.get("shortfall")})
    return {k: v for k, v in f.items() if v is not None}


def prioritise(actions: list[dict], cand_map: dict, limit: int) -> list[dict]:
    """Which moves deserve a review: sign-off moves, ageing, unusual/low-confidence, ones with alternatives, then by value."""
    def score(a):
        return (int(a["requires_approval"]) + int("ageing" in a["reason"] or a["reason"] == "over_capacity") + int(bool({"low_confidence", "peer_estimate"} & set(a.get("flags", []))))
                + int(bool(cand_map.get(a["id"]))) + int(a["value_mrp"] >= 50000))
    def best_delta(a):
        cs = cand_map.get(a["id"]) or []
        return max((c["metrics"]["delta_expected_revenue"] for c in cs), default=None)
    # reserve a third of the budget for moves whose calculated best alternative is at least as good as the original
    promising = sorted([a for a in actions if (best_delta(a) is not None and best_delta(a) >= 0)], key=lambda a: (-best_delta(a), a["id"]))[: max(limit // 3, 1)]
    taken = {a["id"] for a in promising}
    rest = sorted([a for a in actions if a["id"] not in taken], key=lambda a: (-score(a), -a["value_mrp"], a["id"]))
    return (promising + rest)[:limit]


_NUM = re.compile(r"(?<![\w.-])-?\d[\d,]*(?:\.\d+)?")
_ID = re.compile(r"\b[A-Z]{2,5}-[A-Z0-9-]+\b|\b\d{4}-\d{2}-\d{2}\b|\bNDL-\d+\b")


def allowed_numbers(obj) -> set:
    out = set()
    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            for nd in (0, 1, 2):
                out.add(round(float(x), nd))
            out.add(float(int(x)) if float(x) == int(x) else round(float(x), 2))
            if abs(x) >= 100:
                out.add(round(x / 1e5, 1)); out.add(round(x / 1e5, 2))     # "₹1.2 lakh" style restatements
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            for m in _NUM.findall(_ID.sub(" ", x)):
                try:
                    out.add(round(float(m.replace(",", "")), 2))
                except ValueError:
                    pass
    walk(obj)
    return out


def ungrounded_numbers(review: AIReview, grounding: dict) -> list:
    """Numbers the model wrote that are not present in the facts/candidates it was given (hallucination guard)."""
    text = " ".join([review.plain_explanation, review.reasoning, *review.risks, *review.missing_information])
    ok = allowed_numbers(grounding)
    bad = []
    for m in _NUM.findall(_ID.sub(" ", text)):
        try:
            v = round(float(m.replace(",", "")), 2)
        except ValueError:
            continue
        if v not in ok and round(v) not in ok and round(v, 1) not in ok:
            bad.append(m)
    return bad


def prompt_for(facts: dict, cands: list[dict]) -> str:
    cs = [{"id": c["id"], "kind": c["kind"], "from": c["source"], "to": c["destination"], "units": c["qty"], "lead_days": c["lead_days"],
           "calculated_revenue_change_rupees": c["metrics"]["delta_expected_revenue"], "source_cover_days_after": c["metrics"].get("source_cover_after_days"),
           "destination_cover_days_after": c["metrics"].get("dest_cover_after_days"), "passes_all_hard_rules": True} for c in cands]
    return json.dumps({"facts": facts, "candidates": cs}, ensure_ascii=False, default=str)


def review_one(client: OllamaClient, a: dict, evidence: dict, cands: list[dict], policy: dict) -> dict:
    """Returns {state, review, alternative, error}. Never raises."""
    facts = build_facts(a, evidence, policy)
    user = prompt_for(facts, cands)
    grounding = json.loads(user)
    cand_ids = {c["id"]: c for c in cands}
    try:
        for attempt in (1, 2):
            raw = client.chat_json(SYSTEM, user + ("" if attempt == 1 else "\nIMPORTANT: your previous answer was rejected: quote only numbers that appear in the facts, and give evidence_refs using exact keys from `facts`."), REVIEW_SCHEMA)
            rv = AIReview.model_validate(raw)
            bad = ungrounded_numbers(rv, grounding)
            bad += [f"ref:{x}" for x in rv.evidence_refs if x not in facts and x not in cand_ids]
            if not rv.evidence_refs:
                bad.append("no evidence_refs")
            if not bad:
                break
            log.warning("move %s: rejected response %s (attempt %d)", a["id"], bad[:4], attempt)
        else:
            return {"state": "failed", "error": f"rejected: ungrounded numbers or missing/invalid evidence references {bad[:4]}"}
    except OllamaError as e:
        return {"state": "failed", "error": f"inference unavailable: {e}"[:300]}
    except Exception as e:  # noqa: BLE001  (pydantic ValidationError etc.)
        return {"state": "failed", "error": f"malformed response: {type(e).__name__}"}
    outcome, note, alt = rv.outcome, None, None
    if outcome == "ALTERNATIVE":
        c = cand_ids.get(rv.preferred_candidate or "")
        if not c:
            outcome, note = "FLAG", "model named an alternative that is not one of the validated candidates; downgraded to FLAG"
        elif c["metrics"]["delta_expected_revenue"] < 0:
            outcome, note = "FLAG", "calculation does not support the model's preferred alternative (lower expected revenue); downgraded to FLAG"
        else:
            alt = c
    d = rv.model_dump()
    d["outcome"] = outcome
    d["deterministic_note"] = note
    d["model_confidence_note"] = "model-stated, not a calibrated probability"
    d["facts_used"] = facts
    d["candidates_considered"] = [{"id": c["id"], "kind": c["kind"], "source": c["source"], "destination": c["destination"], "qty": c["qty"], "lead_days": c["lead_days"],
                                   "delta_expected_revenue": c["metrics"]["delta_expected_revenue"], "validated": True} for c in cands]
    return {"state": "completed", "review": d, "alternative": alt, "error": None}


def evidence_hash(facts_json: str, model: str) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{model}|{facts_json}".encode()).hexdigest()


def review_run(run_id: str, actions: list[dict], evidence_by_id: dict, cand_map: dict, policy: dict, client: OllamaClient | None = None, limit: int | None = None) -> dict:
    """Review the highest-priority moves one by one (single local GPU), caching by evidence hash. Safe to re-run."""
    client = client or OllamaClient()
    limit = int(limit if limit is not None else os.environ.get("AI_REVIEW_LIMIT", 40))
    chosen = prioritise(actions, cand_map, limit)
    chosen_ids = {a["id"] for a in chosen}
    for a in actions:
        if a["id"] not in chosen_ids:
            db.set_review(run_id, a["id"], "skipped", error="outside the review budget (lower priority)", model=client.model)
    health = client.available()
    if not health["ok"]:
        for a in chosen:
            db.set_review(run_id, a["id"], "pending", error=f"AI unavailable: {health.get('error')}", model=client.model)
        db.update_run(run_id, ai_status="pending")
        log.warning("Ollama unavailable (%s): AI review left pending; deterministic plan is unaffected", health.get("error"))
        return {"ai_status": "pending", "completed": 0, "failed": 0, "skipped": len(actions) - len(chosen), "pending": len(chosen), "reason": health.get("error")}
    done = failed = cached = 0
    for a in chosen:
        ev = evidence_by_id.get(a["id"], {})
        cands = cand_map.get(a["id"], [])
        facts_json = prompt_for(build_facts(a, ev, policy), cands)
        h = evidence_hash(facts_json, client.model)
        hit = db.cached_review(h, client.model)
        if hit:
            db.set_review(run_id, a["id"], "completed", evidence_hash=h, outcome=hit["outcome"], review=hit["review"], alternative=hit["alternative"], model=client.model)
            done += 1; cached += 1
            continue
        db.set_review(run_id, a["id"], "processing", evidence_hash=h, model=client.model)
        res = review_one(client, a, ev, cands, policy)
        if res["state"] == "completed":
            db.set_review(run_id, a["id"], "completed", evidence_hash=h, outcome=res["review"]["outcome"], review=res["review"], alternative=res["alternative"], error=None, model=client.model)
            done += 1
        else:
            db.set_review(run_id, a["id"], "failed", evidence_hash=h, error=res["error"], model=client.model)
            failed += 1
    status = "completed" if failed == 0 else ("partial" if done else "failed")
    db.update_run(run_id, ai_status=status)
    return {"ai_status": status, "completed": done, "cached": cached, "failed": failed, "skipped": len(actions) - len(chosen), "pending": 0}
