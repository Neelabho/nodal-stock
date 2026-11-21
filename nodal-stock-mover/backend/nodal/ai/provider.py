"""One AI entry point for every feature. Sarvam AI is the model. (A local Ollama model is opt-in only: AI_USE_LOCAL=1; it is OFF by default.)

Every feature (move reviews, shipment summaries, shortage actions, plan answers) calls this the same way it called OllamaClient, and
every answer still goes through the same schema / number-grounding / evidence checks, whichever model wrote it.

Privacy: the Sarvam fallback receives the same prompt the local model would have received (store, product and number facts). It is ON by
default at the builder's request; set AI_REMOTE_FALLBACK=0 to keep everything local. A per-process call cap (AI_REMOTE_MAX_CALLS) limits use.
"""
from __future__ import annotations
import json, logging, os, re, time

from .ollama import OllamaClient, OllamaError

log = logging.getLogger("nodal.ai")


class AIProvider:
    def __init__(self, local: OllamaClient | None = None, remote=None, allow_remote: bool | None = None, local_retry_seconds: float = 30, use_local: bool | None = None):
        from ..qa import SarvamClient
        if use_local is None:
            use_local = local is not None or os.environ.get("AI_USE_LOCAL") == "1"
        self.local = (local or OllamaClient()) if use_local else None
        self.remote = remote or SarvamClient()
        self.allow_remote = (os.environ.get("AI_REMOTE_FALLBACK", "1") != "0") if allow_remote is None else allow_remote
        self.max_remote = int(os.environ.get("AI_REMOTE_MAX_CALLS", 200))
        self.remote_calls = 0
        self.model = self.local.model if self.local else self.remote.model
        self.last_source = "sarvam"
        self._local_ok_until = 0.0
        self._local_bad_until = 0.0
        self._retry = local_retry_seconds

    # ---- availability
    def _local_ok(self) -> bool:
        if self.local is None:
            return False
        now = time.time()
        if now < self._local_bad_until:
            return False
        if now < self._local_ok_until:
            return True
        ok = bool(self.local.available().get("ok"))
        if ok:
            self._local_ok_until = now + self._retry
        else:
            self._local_bad_until = now + self._retry
        return ok

    def _remote_ok(self) -> bool:
        return bool(self.allow_remote and self.remote.configured and self.remote_calls < self.max_remote)

    def available(self) -> dict:
        lo, re_ = self._local_ok(), self._remote_ok()
        self.model = self.local.model if lo else self.remote.model
        return {"ok": lo or re_, "local": lo, "remote": re_, "model": self.model}

    # ---- the call
    def chat_json(self, system: str, user: str, schema: dict, num_ctx: int = 4096, max_tokens: int = 700) -> dict:
        problems = []
        if self._local_ok():
            try:
                out = self.local.chat_json(system, user, schema, num_ctx=num_ctx, max_tokens=max_tokens)
                self.last_source, self.model = "local_ai", self.local.model
                return out
            except OllamaError as e:
                problems.append(f"local: {e}")
                self._local_bad_until = time.time() + self._retry            # stop hammering a model that just failed
                self._local_ok_until = 0.0
                log.warning("local AI failed; trying Sarvam: %s", str(e)[:120])
        if self._remote_ok():
            self.remote_calls += 1
            try:
                text = self.remote.chat(system + "\nReply with ONLY a single JSON object that matches this JSON schema, no other text:\n" + json.dumps(schema),
                                        user, max_tokens=max(max_tokens, 500), temperature=0.1)
                try:
                    out = _extract_json(text)
                except ValueError:
                    out = _wrap_single_text(text, schema)          # Sarvam sometimes answers in prose; wrap it when the schema is one text field
                self.last_source, self.model = "sarvam", self.remote.model
                return out
            except Exception as e:  # noqa: BLE001
                problems.append(f"sarvam: {type(e).__name__}")
        raise OllamaError("no AI model available (" + "; ".join(problems or ["Sarvam is off (AI_REMOTE_FALLBACK=0) or has no API key"]) + ")")


def _extract_json(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no JSON object in answer")
    return json.loads(m.group(0))


def _wrap_single_text(text: str, schema: dict) -> dict:
    req = schema.get("required", [])
    props = schema.get("properties", {})
    if len(req) == 1 and props.get(req[0], {}).get("type") == "string" and (text or "").strip():
        return {req[0]: text.strip().strip('"')}
    raise ValueError("answer was not JSON and the schema has more than one field")


def default_client() -> AIProvider:
    return AIProvider()
