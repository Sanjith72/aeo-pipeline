"""
Provider-agnostic LLM client used across the codebase.

``LLMClient`` is a thin facade: it picks a backend from ``LLMCfg.provider`` and
delegates. Backends —

  - ``ollama`` (default): local Ollama ``/api/generate`` — free, offline, dev.
  - ``cloud``: any single OpenAI-compatible ``/chat/completions`` endpoint (legacy).
  - ``gemini`` / ``qwen``: one native provider, pinned (with 429/5xx retry).
  - ``hybrid``: the Gemini+Qwen router (``nlp/providers.py``) — routes by task
    profile (``reasoning`` → Gemini first, ``fast`` → Qwen first), retries
    rate limits, fails over between providers, and heals malformed JSON with
    the other provider. The production default for the $0 stack.

Flip per environment with ``AEO__LLM__PROVIDER``; keys via
``AEO__LLM__GEMINI_API_KEY`` / ``AEO__LLM__QWEN_API_KEY`` (or the legacy
``AEO__LLM__CLOUD_API_KEY``). The public surface (``LLMClient``, ``generate``,
``generate_json``, ``enabled``, ``model``, ``get_client``) is unchanged, so
every scorer/pipeline call-site keeps working untouched; ``embed`` is additive.

Design notes:
  - Synchronous on purpose: scoring runs in worker threads, not the async crawl
    loop, and a blocking call keeps the scorers easy to test.
  - Every method returns ``None`` on failure rather than raising, so a down or
    misconfigured provider never breaks a scoring run.
  - ``generate_json`` asks for JSON output and still defends against models that
    wrap the object in prose, using a 3-strategy extraction (plus cross-provider
    healing on the hybrid backend).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Protocol

import httpx

from ..logging import get_logger
from ..settings import LLMCfg, get_settings
from .providers import HybridBackend, OpenAICompatBackend

log = get_logger(__name__)

# Connect-phase ceiling for the local Ollama call: a DOWN daemon must fail in seconds, not
# wait out the (much longer) generation read timeout. A fail-fast floor, not a tunable — the
# read timeout itself stays configurable (LLMCfg.timeout_sec / the shorter interactive_timeout_sec
# used by the foreground page-building paths).
_CONNECT_TIMEOUT_SEC = 5.0


class _Backend(Protocol):
    @property
    def model(self) -> str: ...

    def generate(self, prompt: str, system: str | None, *, json_mode: bool) -> str | None: ...


class _OllamaBackend:
    """Local Ollama via ``/api/generate``."""

    def __init__(self, cfg: LLMCfg) -> None:
        self._cfg = cfg
        self.model = cfg.model

    def generate(self, prompt: str, system: str | None, *, json_mode: bool) -> str | None:
        payload: dict[str, Any] = {
            "model": self._cfg.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self._cfg.temperature,
                "num_predict": self._cfg.num_predict,
            },
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        from ..crawl.transport import sync_transport

        # Fail-fast: bound the slow generation read by the configured timeout AND cap the
        # connect phase, so a DOWN Ollama errors out in seconds instead of waiting the full
        # timeout. One attempt, no retry — the deterministic scaffold is the safety net
        # (deterministic-first contract), so a hung/slow model must never block a run.
        timeout = httpx.Timeout(
            self._cfg.timeout_sec,
            connect=min(float(self._cfg.timeout_sec), _CONNECT_TIMEOUT_SEC),
        )
        try:
            with httpx.Client(timeout=timeout, transport=sync_transport()) as client:
                resp = client.post(f"{self._cfg.host}/api/generate", json=payload)
                resp.raise_for_status()
                return resp.json().get("response", "").strip()
        except Exception as exc:  # never let the LLM break a run; one try only, no retry
            log.warning("llm_generate_failed", provider="ollama",
                        model=self._cfg.model, error=str(exc))
            return None


def _make_backend(cfg: LLMCfg, profile: str = "fast") -> _Backend | None:
    """Build the backend for ``cfg.provider`` (``None`` = nothing usable → the client
    reports itself disabled and every call degrades to deterministic output)."""
    if cfg.provider == "cloud":
        return OpenAICompatBackend(
            cfg, name="cloud", base_url=cfg.cloud_base_url,
            model=cfg.cloud_model, api_key=cfg.cloud_api_key,
        )
    if cfg.provider == "gemini":
        return OpenAICompatBackend(
            cfg, name="gemini", base_url=cfg.gemini_base_url,
            model=cfg.gemini_model, api_key=cfg.gemini_api_key,
        ) if cfg.gemini_api_key else None
    if cfg.provider == "qwen":
        return OpenAICompatBackend(
            cfg, name="qwen", base_url=cfg.qwen_base_url,
            model=cfg.qwen_model, api_key=cfg.qwen_api_key,
        ) if cfg.qwen_api_key else None
    if cfg.provider == "hybrid":
        hybrid = HybridBackend(cfg, profile=profile)
        return hybrid if hybrid.available else None
    return _OllamaBackend(cfg)


class LLMClient:
    """Facade over a provider backend. Returns ``None`` on failure or when disabled.

    ``profile`` only matters for the hybrid backend: ``fast`` (default) runs Qwen
    first, ``reasoning`` runs Gemini first (used by the planning/agent tier)."""

    def __init__(self, cfg: LLMCfg, *, profile: str = "fast") -> None:
        self._cfg = cfg
        self.profile = profile
        self._backend: _Backend | None = _make_backend(cfg, profile) if cfg.enabled else None

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled and self._backend is not None

    @property
    def provider(self) -> str:
        return self._cfg.provider

    @property
    def model(self) -> str:
        return self._backend.model if self._backend else self._cfg.model

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False) -> str | None:
        backend = self._backend
        if backend is None or not self._cfg.enabled:
            return None
        return backend.generate(prompt, system, json_mode=json_mode)

    def generate_json(self, prompt: str, system: str | None = None) -> dict[str, Any] | None:
        backend = self._backend
        if backend is None or not self._cfg.enabled:
            return None
        if isinstance(backend, HybridBackend):
            # Hybrid path: failover + cross-provider healing live in the router.
            return backend.generate_json(prompt, system, extract=_extract_json)
        raw = backend.generate(prompt, system, json_mode=True)
        if not raw:
            return None
        return _extract_json(raw)

    def embed(self, text: str) -> list[float] | None:
        """One 768-dim embedding vector, or ``None`` when the backend has no embeddings
        surface (Ollama, OpenRouter free models) — callers degrade to keyword search."""
        backend = self._backend
        if backend is None or not self._cfg.enabled:
            return None
        embedder = getattr(backend, "embed", None)
        if embedder is None:
            return None
        return embedder(text)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Three escalating strategies to pull a JSON object out of model output."""
    # 1. Whole string is valid JSON.
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2. First {...} span by regex.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 3. Balanced-brace scan (handles trailing prose / nested objects).
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    start = -1
    return None


@lru_cache(maxsize=1)
def get_client() -> LLMClient:
    return LLMClient(get_settings().llm)


@lru_cache(maxsize=1)
def get_bulk_client() -> LLMClient:
    """The client for BURST paths — the async deep audit's per-page scoring/analysis, which
    fires many calls and trips cloud rate limits. Routed to ``AEO__LLM__BULK_PROVIDER`` (e.g.
    local ``ollama``, using the existing ``host``/``model``) so the audit runs un-throttled.
    Falls back to the primary ``get_client()`` when unset or equal to the primary provider."""
    cfg = get_settings().llm
    if not cfg.bulk_provider or cfg.bulk_provider == cfg.provider:
        return get_client()
    return LLMClient(cfg.model_copy(update={"provider": cfg.bulk_provider}))


@lru_cache(maxsize=1)
def get_interactive_client() -> LLMClient:
    """The client for FOREGROUND, latency-sensitive LLM work — the synchronous ``/api/plan``
    brief→blueprint call and the polled ``/api/deliverables/personalize`` build. Same
    provider/model/host as :func:`get_client`, but with a SHORT, fail-fast generation timeout
    (``interactive_timeout_sec``) so a slow or hung local model degrades to deterministic
    output in bounded time instead of making a user wait minutes. Distinct from the bulk/audit
    path (:func:`get_bulk_client`), which keeps the full ``timeout_sec`` (fire-and-forget
    batch). Retries are pinned to a single attempt so no backoff sleep ever runs inside
    a user-facing call — a hybrid chain still fails over, but each backend gets exactly
    one fast try. Falls back to the primary client when nothing would differ."""
    cfg = get_settings().llm
    if cfg.interactive_timeout_sec >= cfg.timeout_sec and cfg.retry_max_attempts <= 1:
        return get_client()
    return LLMClient(cfg.model_copy(update={
        "timeout_sec": min(cfg.interactive_timeout_sec, cfg.timeout_sec),
        "retry_max_attempts": 1,
    }))


@lru_cache(maxsize=1)
def get_planning_client() -> LLMClient:
    """The reasoning/drafting tier for the agent layer (Planner/Builder). Routed to
    ``AEO__LLM__PLANNING_PROVIDER`` (e.g. ``cloud`` for a frontier model). On the
    ``hybrid`` provider this is the ``reasoning`` profile — Gemini first, Qwen as
    failover — while the fast sync endpoints keep the ``fast`` (Qwen-first) profile.
    Falls back to the primary ``get_client()`` when nothing distinguishes it."""
    cfg = get_settings().llm
    if not cfg.planning_provider or cfg.planning_provider == cfg.provider:
        if cfg.provider == "hybrid":
            return LLMClient(cfg, profile="reasoning")
        return get_client()
    return LLMClient(
        cfg.model_copy(update={"provider": cfg.planning_provider}), profile="reasoning"
    )
