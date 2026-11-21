"""Local Ollama client. No third-party LLM API: the only endpoint is OLLAMA_BASE_URL (default: local Ollama).

Env: OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT (seconds), OLLAMA_RETRIES, OLLAMA_API_KEY (optional bearer for an authenticated proxy).
"""
from __future__ import annotations
import json, logging, os, time
import httpx

log = logging.getLogger("nodal.ai")


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, base_url=None, model=None, timeout=None, retries=None, api_key=None, transport=None):
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen3:8b")
        self.timeout = float(timeout or os.environ.get("OLLAMA_TIMEOUT", 90))
        self.retries = int(retries if retries is not None else os.environ.get("OLLAMA_RETRIES", 2))
        key = api_key or os.environ.get("OLLAMA_API_KEY")
        self._http = httpx.Client(base_url=self.base_url, timeout=self.timeout, headers={"Authorization": f"Bearer {key}"} if key else None, transport=transport)

    def available(self) -> dict:
        """Cheap health probe: server reachable and the configured model installed."""
        try:
            r = self._http.get("/api/tags", timeout=min(5.0, self.timeout))
            r.raise_for_status()
            names = [m.get("name") for m in r.json().get("models", [])]
            ok = any(n == self.model or (n or "").split(":")[0] == self.model for n in names)
            return {"ok": ok, "model": self.model, "installed": names, **({} if ok else {"error": "model_not_installed"})}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "model": self.model, "error": f"{type(e).__name__}: {e}"[:200]}

    def chat_json(self, system: str, user: str, schema: dict, num_ctx=4096, max_tokens=700) -> dict:
        """One structured-JSON chat call (thinking disabled, temperature 0). Retries on timeouts, 5xx and unparseable JSON."""
        body = {"model": self.model, "stream": False, "think": False, "format": schema, "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        last = None
        for attempt in range(self.retries + 1):
            t0 = time.time()
            try:
                r = self._http.post("/api/chat", json=body)
                r.raise_for_status()
                content = r.json()["message"]["content"]
                out = json.loads(content)
                log.info("ollama ok model=%s attempt=%d %.1fs", self.model, attempt + 1, time.time() - t0)
                return out
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
                last = e
                log.warning("ollama attempt %d failed after %.1fs: %s", attempt + 1, time.time() - t0, type(e).__name__)
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                    break                                    # client errors will not fix themselves
                time.sleep(min(2 ** attempt, 4))
        raise OllamaError(f"{type(last).__name__}: {last}"[:300])
