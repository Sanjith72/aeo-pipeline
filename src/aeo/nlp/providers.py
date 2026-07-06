"""
Native Gemini + Qwen provider backends and the hybrid failover router.

``OpenAICompatBackend`` is one OpenAI-compatible ``/chat/completions`` provider with a
bounded retry policy tuned for free-tier operation:

  - HTTP 429/5xx → exponential backoff and retry within ``retry_max_attempts``,
    honouring ``Retry-After`` up to ``retry_max_backoff_sec``. A longer server-demanded
    wait aborts immediately — with a second provider available, failing over beats
    stalling a worker thread for a minute.
  - Timeouts / transport errors → **no** in-backend retry. A timeout already consumed
    the caller's latency budget; the router fails over instead.
  - Auth/4xx/parse errors → fatal for this call, returns ``None`` (the facade's
    deterministic-first contract: LLM failures never raise into scoring runs).

``HybridBackend`` chains two of them by task profile — ``reasoning`` runs Gemini first
(planning, blueprint synthesis, agent steps), ``fast`` runs Qwen first (per-page
refinement, drafting, classification) — and moves down the chain when a provider fails.
For JSON calls it also **heals**: when a provider returns malformed JSON, the other
provider is asked to repair the raw output before the router gives up on it.

Model/endpoint defaults live in :class:`aeo.settings.LLMCfg` and target the $0 tiers
(Gemini via AI Studio keys, Qwen via OpenRouter ``:free`` models).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..logging import get_logger
from ..settings import LLMCfg

log = get_logger(__name__)

# Connect-phase ceiling: an unreachable endpoint must fail in seconds, not wait out the
# generation read timeout (mirrors the Ollama backend's fail-fast floor).
_CONNECT_TIMEOUT_SEC = 5.0

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})

# OpenRouter attribution headers (their etiquette for free-tier traffic).
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/Sanjith72/aeo-pipeline",
    "X-Title": "AEO Studio",
}

# Matches vector(768) in migration 0025 / storage.repos.embeddings.EMBEDDING_DIM.
_EMBEDDING_DIM = 768

_REPAIR_SYSTEM = (
    "You repair malformed JSON. Output ONLY the corrected JSON object — no prose, "
    "no code fences, no explanations."
)
_REPAIR_PROMPT = (
    "The text below was supposed to be exactly one valid JSON object but is malformed "
    "or wrapped in prose. Reconstruct the intended object faithfully — fix quoting, "
    "commas, and braces, strip surrounding text, and do not invent new fields.\n\n"
    "TEXT:\n{raw}"
)


# Reasoning/thinking models (Qwen3 on Groq, DeepSeek-R1, …) prefix replies with a
# <think>…</think> block. That's chain-of-thought, not the answer — stripped so it can
# never leak into page drafts or JSON extraction. Unclosed tags (truncated output)
# drop everything from <think> on.
_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _retry_after_sec(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:  # HTTP-date form — rare on LLM APIs; treat as unknown
        return None


class OpenAICompatBackend:
    """One OpenAI-compatible ``/chat/completions`` provider (Gemini compat endpoint,
    OpenRouter, DashScope, OpenAI, …) with the retry policy described in the module
    docstring. Returns ``None`` on any terminal failure — never raises to callers."""

    def __init__(
        self,
        cfg: LLMCfg,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._cfg = cfg
        self.name = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._extra_headers = extra_headers or {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key or ''}", **self._extra_headers}

    def _timeout(self) -> httpx.Timeout:
        read = float(self._cfg.timeout_sec)
        return httpx.Timeout(read, connect=min(read, _CONNECT_TIMEOUT_SEC))

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response | None:
        """One POST with 429/5xx retry. ``None`` = terminal transport/timeout failure."""
        from ..crawl.transport import sync_transport

        delay = max(0.1, self._cfg.retry_initial_backoff_sec)
        cap = max(delay, self._cfg.retry_max_backoff_sec)
        attempts = max(1, self._cfg.retry_max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=self._timeout(), transport=sync_transport()) as client:
                    resp = client.post(
                        f"{self._base_url}{path}", json=payload, headers=self._headers()
                    )
            except Exception as exc:  # timeout/DNS/conn-reset: fail fast, router fails over
                log.warning("llm_transport_failed", provider=self.name, model=self.model,
                            error=str(exc), attempt=attempt)
                return None

            if resp.status_code not in _RETRYABLE_STATUS:
                return resp
            server_wait = _retry_after_sec(resp)
            if attempt == attempts or (server_wait is not None and server_wait > cap):
                # Budget spent, or the server demands a longer wait than we tolerate —
                # surface the failure so the hybrid router can switch providers now.
                log.warning("llm_retries_exhausted", provider=self.name, model=self.model,
                            status=resp.status_code, attempts=attempt, retry_after=server_wait)
                return resp
            wait = min(cap, max(delay, server_wait or 0.0))
            log.info("llm_retrying", provider=self.name, model=self.model,
                     status=resp.status_code, attempt=attempt, wait_sec=round(wait, 2))
            time.sleep(wait)
            delay = min(cap, delay * 2)
        return None  # pragma: no cover — loop always returns

    def generate(self, prompt: str, system: str | None, *, json_mode: bool) -> str | None:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.num_predict,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = self._post("/chat/completions", payload)
        if resp is None:
            return None
        try:
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if not content:
                return None
            cleaned = _strip_reasoning(content)
            return cleaned if cleaned else None
        except Exception as exc:  # 4xx/response-shape problems are terminal for this call
            log.warning("llm_generate_failed", provider=self.name, model=self.model,
                        status=resp.status_code, error=str(exc))
            return None

    def embed(self, text: str) -> list[float] | None:
        """One embedding vector via the provider's ``/embeddings`` surface (Gemini's
        compat endpoint supports it; OpenRouter free models generally don't — that just
        returns ``None`` and callers degrade to keyword search)."""
        payload = {
            "model": self._cfg.embedding_model,
            "input": text,
            "dimensions": _EMBEDDING_DIM,
        }
        resp = self._post("/embeddings", payload)
        if resp is None:
            return None
        try:
            resp.raise_for_status()
            vector = resp.json()["data"][0]["embedding"]
            if not isinstance(vector, list) or len(vector) != _EMBEDDING_DIM:
                raise ValueError(f"expected {_EMBEDDING_DIM}-dim list")
            return [float(x) for x in vector]
        except Exception as exc:
            log.warning("llm_embed_failed", provider=self.name,
                        model=self._cfg.embedding_model, error=str(exc))
            return None


def _compat(
    cfg: LLMCfg, name: str, base_url: str, model: str, api_key: str | None
) -> OpenAICompatBackend | None:
    if not api_key:
        return None
    extra = _OPENROUTER_HEADERS if "openrouter" in base_url else None
    return OpenAICompatBackend(
        cfg, name=name, base_url=base_url, model=model, api_key=api_key, extra_headers=extra
    )


def _gemini_chain(cfg: LLMCfg, profile: str) -> list[OpenAICompatBackend]:
    """Gemini with its in-family fallback. Reasoning wants quality first (flash →
    flash-lite); fast wants quota first (flash-lite → flash — lite has ~4× the free
    requests-per-day, so bulk work spends the cheap pool before touching flash)."""
    models = [cfg.gemini_model, cfg.gemini_fallback_model]
    if profile != "reasoning":
        models.reverse()
    seen: list[str] = []
    chain = []
    for model in models:
        if not model or model in seen:
            continue
        seen.append(model)
        backend = _compat(cfg, "gemini", cfg.gemini_base_url, model, cfg.gemini_api_key)
        if backend is not None:
            chain.append(backend)
    return chain


def _qwen_chain(cfg: LLMCfg) -> list[OpenAICompatBackend]:
    """Qwen primary (Groq free plan by default) plus the optional OpenRouter ':free'
    fallback — a separate endpoint AND key, so a Groq outage or exhausted daily quota
    doesn't take the whole family down."""
    chain = []
    primary = _compat(cfg, "qwen", cfg.qwen_base_url, cfg.qwen_model, cfg.qwen_api_key)
    if primary is not None:
        chain.append(primary)
    fallback = _compat(
        cfg, "qwen-fallback", cfg.qwen_fallback_base_url,
        cfg.qwen_fallback_model, cfg.qwen_fallback_api_key,
    )
    if fallback is not None:
        chain.append(fallback)
    return chain


class HybridBackend:
    """Gemini + Qwen behind one backend: profile-based routing, automatic failover
    (in-family first, then across families), and cross-provider JSON healing.
    Providers whose key is unset simply drop out of the chain; with no keys at all the
    backend reports itself unavailable and the facade degrades to deterministic output."""

    def __init__(self, cfg: LLMCfg, profile: str = "fast") -> None:
        self._cfg = cfg
        self.profile = profile
        gemini, qwen = _gemini_chain(cfg, profile), _qwen_chain(cfg)
        ordered = gemini + qwen if profile == "reasoning" else qwen + gemini
        self._chain: list[OpenAICompatBackend] = ordered
        if not cfg.failover_enabled:
            self._chain = self._chain[:1]

    @property
    def available(self) -> bool:
        return bool(self._chain)

    @property
    def model(self) -> str:
        return self._chain[0].model if self._chain else self._cfg.gemini_model

    def generate(self, prompt: str, system: str | None, *, json_mode: bool) -> str | None:
        for i, backend in enumerate(self._chain):
            out = backend.generate(prompt, system, json_mode=json_mode)
            if out is not None:
                if i:
                    log.info("llm_failover_recovered", provider=backend.name, profile=self.profile)
                return out
            if i + 1 < len(self._chain):
                log.warning("llm_failover", from_provider=backend.name,
                            to_provider=self._chain[i + 1].name, profile=self.profile)
        return None

    def generate_json(
        self,
        prompt: str,
        system: str | None,
        *,
        extract: Callable[[str], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        """JSON with healing: a provider that answers with malformed JSON gets its raw
        output repaired by the OTHER provider before the chain moves on."""
        for i, backend in enumerate(self._chain):
            raw = backend.generate(prompt, system, json_mode=True)
            if raw is None:
                if i + 1 < len(self._chain):
                    log.warning("llm_failover", from_provider=backend.name,
                                to_provider=self._chain[i + 1].name, profile=self.profile)
                continue
            obj = extract(raw)
            if obj is not None:
                return obj
            healer = self._chain[i + 1] if i + 1 < len(self._chain) else backend
            log.warning("llm_json_healing", provider=backend.name, healer=healer.name,
                        profile=self.profile, raw_chars=len(raw))
            repaired = healer.generate(
                _REPAIR_PROMPT.format(raw=raw[:6000]), _REPAIR_SYSTEM, json_mode=True
            )
            if repaired:
                obj = extract(repaired)
                if obj is not None:
                    return obj
        return None

    def embed(self, text: str) -> list[float] | None:
        # Gemini first regardless of profile — it's the one with a real (and free)
        # embeddings surface; anything else in the chain is a best-effort fallback.
        ordered = sorted(self._chain, key=lambda b: b.name != "gemini")
        for backend in ordered:
            vector = backend.embed(text)
            if vector is not None:
                return vector
        return None
