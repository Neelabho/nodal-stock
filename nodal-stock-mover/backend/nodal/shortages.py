"""Shortages = demand the plan could not cover (no warehouse stock, no store can spare any, or the store is full).

Grouped by store (about 20 groups instead of hundreds of lines), each with a plain-English business action. The action is written by the
local AI (Sarvam if the local model is unavailable), checked so every number comes from NODAL's facts, and cached; a deterministic
template is the fallback.
"""
from __future__ import annotations
import hashlib, json, logging
from collections import Counter, defaultdict

from . import db

log = logging.getLogger("nodal.shortages")
VERSION = "t2"
CAUSE = {"store_full": "the store is full", "no_warehouse_stock": "the warehouse has none", "no_store_spare": "no other store can spare any", "limited_by_supply_order": "supply ran out in the plan"}


def inbound_map(inbound_rows, as_of) -> dict:
    """sku -> {'qty': total planned, 'first': earliest planned date, 'first_qty'} for deliveries after the plan date."""
    m = {}
    for r in inbound_rows:
        d = str(r["expected_date"])[:10]
        if d <= str(as_of)[:10]:
            continue
        cur = m.setdefault(r["sku_id"], {"qty": 0, "first": d, "first_qty": 0})
        cur["qty"] += int(r["qty"])
        if d < cur["first"]:
            cur["first"], cur["first_qty"] = d, int(r["qty"])
        elif d == cur["first"]:
            cur["first_qty"] += int(r["qty"])
    return m


def build(unresolved: list[dict], stores: list[dict], inbound: dict, seas: dict) -> list[dict]:
    st = {s["store_id"]: s for s in stores}
    by = defaultdict(list)
    for u in unresolved:
        if u.get("kind") == "unmet_demand":
            by[u["store"]].append(u)
    out = []
    for store, us in by.items():
        s = st.get(store, {})
        fx = seas.get(s.get("state")) or {}
        items = []
        for u in sorted(us, key=lambda x: -x.get("value_mrp", 0)):
            ib = inbound.get(u["sku"])
            items.append({"sku": u["sku"], "style": u.get("style"), "size": u.get("size"), "category": u.get("category"), "units": u["units"], "value_mrp": u.get("value_mrp", 0),
                          "reasons": [CAUSE.get(c, c) for c in u.get("reasons", [])], "codes": u.get("reasons", []), "out_of_stock_on_arrival": (u.get("cover_days") or 0) <= 0,
                          "planned_delivery": None if not ib else {"first": ib["first"], "first_qty": ib["first_qty"], "total_qty": ib["qty"]}})
        codes = Counter(c for i in items for c in set(i["codes"]))
        with_ib = [i for i in items if i["planned_delivery"]]
        out.append({"id": f"SHT-{store}", "store": store, "city": s.get("city"), "tier": s.get("tier"), "n_items": len(items), "units_short": sum(i["units"] for i in items),
                    "value_mrp": sum(i["value_mrp"] for i in items), "causes": dict(codes), "with_planned_delivery": len(with_ib),
                    "first_delivery": min((i["planned_delivery"]["first"] for i in with_ib), default=None), "out_of_stock_items": sum(1 for i in items if i["out_of_stock_on_arrival"]),
                    "categories": dict(Counter(i["category"] for i in items)), "festival": fx.get("festival"), "festival_in_days": fx.get("offset_days"), "items": items})
    out.sort(key=lambda g: (-g["value_mrp"], g["id"]))
    return out


def template_action(g: dict) -> str:
    n = g["n_items"]; fest = f" Diwali is {g['festival_in_days']} days away." if g.get("festival") and (g.get("festival_in_days") or 99) <= 14 else ""
    if g["causes"].get("store_full", 0) * 2 >= n:
        return f"{g['city']} is full, so more stock will not fit. Move slow-selling items out first, then restock the {n} short items.{fest}"
    if g["with_planned_delivery"] * 2 >= n:
        return f"{g['with_planned_delivery']} of the {n} short items already have a warehouse delivery coming (first on {g['first_delivery']}). Wait for it, or ask the warehouse to bring it forward.{fest}"
    return f"The warehouse has none of these items and no other store can spare any. Place a replenishment order for the {n} short items, highest value first.{fest}"


def facts_for(g: dict) -> dict:
    top = [{"item": f"{i['units']} units of {i['style']} size {i['size']}", "why": "; ".join(i["reasons"])} for i in g["items"][:4]]
    f = {"store": g["store"], "city": g["city"], "items_short": g["n_items"], "units_short": g["units_short"], "value_rupees": g["value_mrp"],
         "reasons": {CAUSE.get(k, k): v for k, v in g["causes"].items()}, "items_with_planned_warehouse_delivery": g["with_planned_delivery"], "items_out_of_stock_on_arrival": g["out_of_stock_items"], "top_items": top}
    if g["first_delivery"]:
        f["first_planned_delivery"] = g["first_delivery"]
    if g.get("festival") and (g.get("festival_in_days") or 99) <= 21:
        f.update(festival=g["festival"], festival_in_days=g["festival_in_days"])
    return f


SYSTEM = ("You advise a menswear retailer's inventory manager. Rewrite the draft into one clear business action for this store's shortages, in at most two sentences and under 240 characters. "
          "Choose the action that fits the facts: place a replenishment order, wait for or bring forward a planned warehouse delivery, free space by moving slow stock out, or accept the gap. "
          "Keep every number and date exactly as in the facts or draft; invent no numbers, dates, suppliers or promises. No jargon (no 'cover', 'velocity', 'SKU', 'ledger').")
SCHEMA = {"type": "object", "properties": {"action": {"type": "string", "maxLength": 260}}, "required": ["action"]}


def summarise(groups: list[dict], client=None) -> dict:
    """Fill g['action'] and g['action_source'] ('local_ai' | 'sarvam' | 'template'). Never raises."""
    from .shipments import _grounded
    stats = {"cached": 0, "local_ai": 0, "sarvam": 0, "template": 0}
    available = False
    if client is not None:
        try:
            available = bool(client.available().get("ok"))
        except Exception:  # noqa: BLE001
            available = False
    for g in groups:
        draft = template_action(g)
        facts = {**facts_for(g), "draft": draft}
        fj = json.dumps(facts, sort_keys=True, default=str)
        model = getattr(client, "model", "none") if client is not None else "none"
        key = hashlib.sha256(f"{VERSION}|{model}|{fj}".encode()).hexdigest()
        hit = db.get_text(key) if client is not None else None
        if hit:
            g["action"], g["action_source"] = hit, ("sarvam" if str(model).startswith("sarvam") else "local_ai"); stats["cached"] += 1
            continue
        text = None
        if available:
            for attempt in (1, 2):
                try:
                    raw = client.chat_json(SYSTEM, fj + ("" if attempt == 1 else "\nYour last answer used numbers, dates or words not allowed. Use only the facts."), SCHEMA, max_tokens=200)
                    t = str(raw.get("action", "")).strip()
                    if t and _grounded(t, facts):
                        text = t
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning("shortage action for %s failed: %s", g["id"], type(e).__name__)
                    break
        if text:
            db.put_text(key, "shortage", text, model)
            src = getattr(client, "last_source", "local_ai")
            g["action"], g["action_source"] = text, src; stats[src] = stats.get(src, 0) + 1
        else:
            g["action"], g["action_source"] = draft, "template"; stats["template"] += 1
    return stats
