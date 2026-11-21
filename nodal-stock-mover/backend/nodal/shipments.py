"""Group the moves into store shipments (one decision per store instead of hundreds) and write plain-language summaries.

Summaries are written by the local model (Qwen3 via Ollama), checked so every number comes from NODAL's facts, and cached by a hash of
the facts. If the model is unavailable or its text fails the check, a deterministic template is used (and labelled as such).
"""
from __future__ import annotations
import hashlib, json, logging
from collections import defaultdict

from . import db

log = logging.getLogger("nodal.shipments")
VERSION = "s1"
TIER_NAME = {1: "Critical", 2: "High", 3: "Normal", 4: "Housekeeping"}
PLAIN = {"under_stocked": "running low", "scheduled_inbound": "running low", "display_minimum": "short of its display minimum",
         "ageing_relocation": "receiving older stock that sells here", "surplus_rebalance": "topped up from a store with spare stock",
         "over_capacity": "holding more than it can store", "ageing_pullback": "holding old stock that is not selling"}


def _short(d: str) -> str:
    import datetime as dt
    return dt.date.fromisoformat(d).strftime("%-d %b")


def build(actions: list[dict], stores: list[dict], seas: dict, reviews: dict | None = None, wh_id: str = "WH-KOL") -> list[dict]:
    st = {s["store_id"]: s for s in stores}
    reviews = reviews or {}
    groups = defaultdict(list)
    for a in actions:
        key = ("pullback", a["source"]) if a["type"] == "PULL_BACK" else ("deliver", a["destination"])
        groups[key].append(a)
    out = []
    for (kind, store), ms in groups.items():
        ms.sort(key=lambda a: (a["priority_tier"], -a["value_mrp"], a["id"]))
        s = st.get(store, {})
        fest = (seas.get(s.get("state")) or {}) if s else {}
        reasons = defaultdict(int)
        for a in ms:
            reasons[PLAIN.get(a["reason"], a["reason"])] += 1
        wh_units = sum(a["qty"] for a in ms if a["source"] == wh_id)
        flagged = sum(1 for a in ms if (reviews.get(a["id"]) or {}).get("outcome") == "FLAG")
        signoff = sum(1 for a in ms if a["requires_approval"])
        low_conf = sum(1 for a in ms if "low_confidence" in a.get("flags", []))
        late = sum(1 for a in ms if any(f.startswith("arrives_after") for f in a.get("flags", [])))
        tier = min(a["priority_tier"] for a in ms)
        covers = [a["cover_before_days"] for a in ms if a.get("cover_before_days") is not None and a["type"] != "PULL_BACK"]
        out.append({
            "id": f"{'PBK' if kind == 'pullback' else 'SHP'}-{store}", "kind": kind, "store": store, "city": s.get("city"), "tier": s.get("tier"),
            "move_ids": [a["id"] for a in ms], "n_moves": len(ms), "units": sum(a["qty"] for a in ms), "value_mrp": sum(a["value_mrp"] for a in ms),
            "from_warehouse_units": wh_units, "from_stores_units": sum(a["qty"] for a in ms) - wh_units if kind == "deliver" else 0,
            "reasons": [{"reason": r, "items": n} for r, n in sorted(reasons.items(), key=lambda x: -x[1])],
            "urgency": TIER_NAME[tier], "priority_tier": tier, "earliest_arrival": min(a["arrival_date"] for a in ms), "latest_arrival": max(a["arrival_date"] for a in ms),
            "festival": fest.get("festival"), "festival_in_days": fest.get("offset_days"), "lowest_cover_days": min(covers) if covers else None,
            "needs_signoff": signoff, "ai_flagged": flagged, "low_confidence": low_conf, "arrives_late": late,
            "routine": flagged == 0 and low_conf == 0 and late == 0 and tier != 1,          # big (sign-off) items are still confirmed explicitly on approval
        })
    out.sort(key=lambda x: (x["priority_tier"], -x["value_mrp"], x["id"]))
    return out


def template_summary(sh: dict) -> str:
    if sh["kind"] == "pullback":
        return f"{sh['store']} holds more stock than it can sell. {sh['units']} units go to stores that sell them, or back to the warehouse."
    top = sh["reasons"][0]["reason"] if sh["reasons"] else "running low"
    fest = f" ahead of {sh['festival']}" if sh.get("festival") and (sh.get("festival_in_days") or 99) <= 14 and "low" in top else ""
    src = "the warehouse" if sh["from_stores_units"] == 0 else ("other stores" if sh["from_warehouse_units"] == 0 else "the warehouse and other stores")
    return f"{sh['city']} ({sh['store']}) is {top} on {sh['n_moves']} item{'s' if sh['n_moves'] != 1 else ''}{fest}. {sh['units']} units are on the way from {src}, arriving by {_short(sh['latest_arrival'])}."


def facts_for(sh: dict) -> dict:
    f = {"store": sh["store"], "city": sh["city"], "kind": "send stock to this store" if sh["kind"] == "deliver" else "take stock back from this store", "items": sh["n_moves"], "units": sh["units"],
         "value_rupees": sh["value_mrp"], "units_from_warehouse": sh["from_warehouse_units"], "units_from_other_stores": sh["from_stores_units"],
         "reasons": sh["reasons"], "earliest_arrival": sh["earliest_arrival"], "latest_arrival": sh["latest_arrival"], "items_needing_signoff": sh["needs_signoff"]}
    if sh.get("festival") and (sh.get("festival_in_days") or 99) <= 21:
        f.update(festival=sh["festival"], festival_in_days=sh["festival_in_days"])
    if sh.get("lowest_cover_days") is not None:
        f["lowest_days_of_stock_left"] = round(sh["lowest_cover_days"])
    return f


SYSTEM = ("You write short plain-English summaries for a store manager who must approve or reject a stock shipment. Use ONLY the facts given. "
          "At most two sentences and under 220 characters. No jargon: never write 'cover', 'velocity', 'SKU', 'forecast' or 'ledger'. "
          "Say what is happening and why in everyday words. Any number you write must appear in the facts exactly. Do not tell the reader what to do (no 'approve' or 'restock' advice); just describe what is happening and why. Do not invent anything.")
SCHEMA = {"type": "object", "properties": {"summary": {"type": "string", "maxLength": 240}}, "required": ["summary"]}


def _grounded(text: str, facts: dict) -> bool:
    from .ai.review import allowed_numbers, _NUM, _ID
    ok = allowed_numbers(facts)
    for m in _NUM.findall(_ID.sub(" ", text)):
        try:
            v = round(float(m.replace(",", "")), 2)
        except ValueError:
            continue
        if v not in ok and round(v) not in ok:
            return False
    return len(text) <= 260 and not any(w in text.lower() for w in ("sku", "velocity", "ledger"))


def summarise(shipments: list[dict], client=None) -> dict:
    """Fill sh['summary'] and sh['summary_source']. Returns counts. Never raises."""
    stats = {"cached": 0, "local_ai": 0, "template": 0}
    available = False
    if client is not None:
        try:
            available = client.available()["ok"]
        except Exception:  # noqa: BLE001
            available = False
    for sh in shipments:
        facts = facts_for(sh)
        fj = json.dumps(facts, sort_keys=True, default=str)
        model = getattr(client, "model", "none") if client is not None else "none"
        key = hashlib.sha256(f"{VERSION}|{model}|{fj}".encode()).hexdigest()
        hit = db.get_text(key) if client is not None else None
        if hit:
            sh["summary"], sh["summary_source"] = hit, "local_ai"; stats["cached"] += 1
            continue
        text = None
        if available:
            for attempt in (1, 2):
                try:
                    raw = client.chat_json(SYSTEM, fj + ("" if attempt == 1 else "\nYour last answer used numbers or words not allowed. Use only numbers from the facts and no jargon."), SCHEMA, max_tokens=160)
                    t = str(raw.get("summary", "")).strip()
                    if t and _grounded(t, facts):
                        text = t
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("summary for %s failed: %s", sh["id"], type(e).__name__)
                    break
        if text:
            db.put_text(key, "shipment", text, model)
            sh["summary"], sh["summary_source"] = text, "local_ai"; stats["local_ai"] += 1
        else:
            sh["summary"], sh["summary_source"] = template_summary(sh), "template"; stats["template"] += 1
    return stats
