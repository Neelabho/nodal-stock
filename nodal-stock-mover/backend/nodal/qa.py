"""Ask-a-question: general questions go to Sarvam AI (public facts only); questions about specific stores/moves stay local.

Privacy rule: NO store, SKU, stock or plan numbers are ever sent to Sarvam. The router sends only questions that look general, together with a
fixed block of public facts (the brief's rules and definitions). Plan questions are answered from NODAL's own shipment facts (and the local
Qwen3 model when available). Answers are cached; requests are rate-limited per client and globally.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, logging, os, re
import httpx

from . import db

log = logging.getLogger("nodal.qa")
STORE_ID = re.compile(r"\b[A-Za-z]{3}-\d{2}\b")
SKU_ID = re.compile(r"\b[A-Za-z]{2,4}-\d{2}(?:-[A-Za-z0-9]+)?\b")
PLAN_WORDS = re.compile(r"\b(my|our|today|today's|this (?:shipment|move|plan|store)|the plan|which store|which stores|how many (?:units|moves|shipments)|"
                        r"units (?:to|going)|shipment|shipments|approve|approved|rejected|pending|warehouse stock|next stop)\b", re.I)


class RateLimited(Exception):
    def __init__(self, msg, retry_after=60):
        super().__init__(msg); self.retry_after = retry_after


def normalise(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s₹%-]", " ", q.lower())).strip()


def route(q: str) -> str:
    return "plan" if (STORE_ID.search(q) or SKU_ID.search(q) or PLAN_WORDS.search(q)) else "general"


def public_facts(policy: dict | None) -> str:
    p = policy or {}
    return ("NODAL is a stock-movement planner for a menswear retailer (Manyavar's Stock Mover brief). Definitions: velocity = units a style and size sells per day in a store; "
            "cover = stock divided by daily velocity (days of stock left); minimum display = the least stock a store must keep per category; capacity = most units a store can hold; "
            "in-transit stock counts as arriving and is never sent twice; a store giving stock to another must keep at least "
            f"{p.get('donor_min_cover_days', 30)} days of cover; protected top styles never leave flagship (S++) stores; any single move worth more than "
            f"₹{int(p.get('approval_value_mrp', 100000)):,} at MRP needs human sign-off; pull-back means taking stock from where it does not sell to a store where it does, otherwise back to the warehouse; "
            "approving a plan does not dispatch stock. You do not have access to the company's data, so never state any figure about specific stores, products or the current plan.")


class SarvamClient:
    URL = "https://api.sarvam.ai/v1/chat/completions"

    def __init__(self, api_key=None, model=None, timeout=30, transport=None):
        self.key = api_key or os.environ.get("SARVAM_API_KEY")
        self.model = model or os.environ.get("SARVAM_MODEL", "sarvam-105b-conversations")
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def ask(self, question: str, facts: str) -> str:
        return self.chat(facts + " Answer in at most four plain sentences.", question, max_tokens=350, temperature=0.2)

    def chat(self, system: str, user: str, max_tokens: int = 400, temperature: float = 0.1) -> str:
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        last = None
        for attempt in (1, 2):
            try:
                r = self._http.post(self.URL, headers={"api-subscription-key": self.key, "Content-Type": "application/json"}, json=body)
                if r.status_code == 429:
                    raise RateLimited("The AI service is busy. Try again in a minute.", int(r.headers.get("retry-after", 60) or 60))
                r.raise_for_status()
                text = (r.json()["choices"][0]["message"].get("content") or "").strip()
                if not text:
                    raise ValueError("empty answer")
                return text
            except RateLimited:
                raise
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, ValueError, KeyError) as e:
                last = e
                log.warning("sarvam attempt %d failed: %s", attempt, type(e).__name__)
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    break
        raise RuntimeError(f"AI service unavailable ({type(last).__name__})")


def _limits():
    return (int(os.environ.get("QA_PER_MINUTE", 6)), int(os.environ.get("QA_PER_DAY", 40)), int(os.environ.get("QA_GLOBAL_PER_DAY", 300)))


def _since(**kw) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).isoformat(timespec="seconds")


def plan_answer(question: str, shipments: list[dict], status: dict, local_client=None) -> tuple[str, str]:
    """Answer from NODAL's own shipment facts. Local Qwen3 (if available) phrases it; otherwise a deterministic answer."""
    ids = {m.upper() for m in STORE_ID.findall(question)}
    hit = [s for s in shipments if s["store"].upper() in ids] or []
    if hit:
        facts = [{k: s[k] for k in ("store", "city", "kind", "units", "n_moves", "value_mrp", "urgency", "latest_arrival", "summary", "needs_signoff")} for s in hit[:3]]
        det = " ".join(f"{s['summary']} ({s['n_moves']} items, {s['units']} units, ₹{s['value_mrp']:,}.)" for s in hit[:2])
    else:
        facts = [{"shipments": status.get("shipments"), "units_to_move": status.get("units"), "awaiting_decision": status.get("awaiting"), "need_signoff": status.get("needs_signoff"), "approved": status.get("approved")}]
        det = (f"Today's plan has {status.get('shipments')} shipments and {status.get('units'):,} units to move. {status.get('awaiting'):,} moves are still awaiting a decision and "
               f"{status.get('needs_signoff')} need sign-off.")
    if local_client is not None:
        try:
            if local_client.available()["ok"]:
                raw = local_client.chat_json("Answer the manager's question in at most three plain sentences using ONLY these facts. Never invent numbers.",
                                             json.dumps({"question": question, "facts": facts}, default=str), {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 420}}, "required": ["answer"]}, max_tokens=200)
                from .shipments import _grounded
                t = str(raw.get("answer", "")).strip()
                if t and _grounded(t, {"facts": facts, "q": question}):
                    return t, getattr(local_client, "last_source", "local_ai")
        except Exception as e:  # noqa: BLE001
            log.warning("local answer failed: %s", type(e).__name__)
    return det, "plan"


def answer(question: str, client_id: str, run_id: str, policy: dict | None, shipments: list[dict], status: dict, sarvam: SarvamClient | None = None, local_client=None) -> dict:
    q = (question or "").strip()
    if len(q) < 3:
        raise ValueError("Please type a question.")
    if len(q) > 300:
        raise ValueError("Please keep the question under 300 characters.")
    per_min, per_day, global_day = _limits()
    if db.qa_count(_since(seconds=60), client_id) >= per_min:
        raise RateLimited(f"You are asking too fast. Please wait a moment (limit {per_min} questions a minute).", 30)
    rt = route(q)
    nq = normalise(q)
    qhash = hashlib.sha256(f"{rt}|{run_id if rt == 'plan' else ''}|{nq}".encode()).hexdigest()
    hit = db.qa_get(qhash)
    if hit:
        db.qa_log(client_id, rt, True)
        return {"answer": hit["answer"], "source": "cache", "originally": hit["source"], "route": rt, "cached": True}
    if rt == "plan":
        text, src = plan_answer(q, shipments, status, local_client)
        db.qa_put(qhash, q, text, src, getattr(local_client, "model", "none"))
        db.qa_log(client_id, rt, False)
        return {"answer": text, "source": src, "route": rt, "cached": False}
    sarvam = sarvam or SarvamClient()
    if not sarvam.configured:
        raise RuntimeError("General questions are not available: no Sarvam API key is configured on the server.")
    if db.qa_count(_since(days=1), client_id, uncached_only=True) >= per_day:
        raise RateLimited(f"Daily question limit reached ({per_day}). Previously asked questions are still free.", 3600)
    if db.qa_count(_since(days=1), None, uncached_only=True) >= global_day:
        raise RateLimited("The question service has reached its daily limit. Please try again tomorrow.", 3600)
    text = sarvam.ask(q, public_facts(policy))
    db.qa_put(qhash, q, text, "sarvam", sarvam.model)
    db.qa_log(client_id, rt, False)
    return {"answer": text, "source": "sarvam", "route": rt, "cached": False}


SUGGESTIONS = ["What is stock cover?", "Why does a store need minimum display?", "What does pull-back mean?", "Why do big moves need sign-off?", "How many shipments are waiting today?", "What is going to PAT-01?"]
