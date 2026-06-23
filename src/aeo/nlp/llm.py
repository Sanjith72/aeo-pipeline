"""
Provider-agnostic LLM client used across the codebase.

``LLMClient`` is a thin facade: it picks a backend from ``LLMCfg.provider`` and
delegates. Two backends ship today —

  - ``ollama`` (default): local Ollama ``/api/generate`` — free, offline, dev.
  - ``cloud``: any OpenAI-compatible ``/chat/completions`` endpoint (OpenAI,
    Gemini's compat endpoint, Together, …) — picked in production for quality.

Flip per environment with ``AEO__LLM__PROVIDER=ollama|cloud``; the cloud key
comes from ``AEO__LLM__CLOUD_API_KEY``. The public surface (``LLMClient``,
``generate``, ``generate_json``, ``enabled``, ``model``, ``get_client``) is
unchanged from the old Ollama-only client, so every scorer/pipeline call-site
keeps working untouched.

Design notes:
  - Synchronous on purpose: scoring runs in worker threads, not the async crawl
    loop, and a blocking call keeps the scorers easy to test.
  - Every method returns ``None`` on failure rather than raising, so a down or
    misconfigured provider never breaks a scoring run.
  - ``generate_json`` asks for JSON output and still defends against models that
    wrap the object in prose, using a 3-strategy extraction.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Protocol

import httpx

from ..logging import get_logger
from ..settings import LLMCfg, get_settings

log = get_logger(__name__)

# Connect-phase ceiling for the local Ollama call: a DOWN daemon must fail in seconds, not
# wait out the (much longer) generation read timeout. A fail-fast floor, not a tunable — the
# read timeout itself stays configurable (LLMCfg.timeout_sec / the shorter interactive_timeout_sec
# used by the foreground page-building paths).
_CONNECT_TIMEOUT_SEC = 5.0


class _Backend(Protocol):
    model: str

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


class _CloudBackend:
    """Any OpenAI-compatible ``/chat/completions`` endpoint."""

    def __init__(self, cfg: LLMCfg) -> None:
        self._cfg = cfg
        self.model = cfg.cloud_model

    def generate(self, prompt: str, system: str | None, *, json_mode: bool) -> str | None:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self._cfg.cloud_model,
            "messages": messages,
            "temperature": self._cfg.temperature,
            "max_tokens": self._cfg.num_predict,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._cfg.cloud_api_key or ''}"}

        from ..crawl.transport import sync_transport

        try:
            with httpx.Client(timeout=self._cfg.timeout_sec, transport=sync_transport()) as client:
                resp = client.post(
                    f"{self._cfg.cloud_base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return content.strip() if content else None
        except Exception as exc:  # never let the LLM break a run
            log.warning("llm_generate_failed", provider="cloud",
                        model=self._cfg.cloud_model, error=str(exc))
            return None


def _make_backend(cfg: LLMCfg) -> _Backend:
    if cfg.provider == "cloud":
        return _CloudBackend(cfg)
    return _OllamaBackend(cfg)


class LLMClient:
    """Facade over a provider backend. Returns ``None`` on failure or when disabled."""

    def __init__(self, cfg: LLMCfg) -> None:
        self._cfg = cfg
        self._backend: _Backend | None = _make_backend(cfg) if cfg.enabled else None

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
        raw = self.generate(prompt, system, json_mode=True)
        if not raw:
            return None
        return _extract_json(raw)


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
    batch). Falls back to the primary client when the interactive timeout isn't actually
    shorter."""
    cfg = get_settings().llm
    if cfg.interactive_timeout_sec >= cfg.timeout_sec:
        return get_client()
    return LLMClient(cfg.model_copy(update={"timeout_sec": cfg.interactive_timeout_sec}))


@lru_cache(maxsize=1)
def get_planning_client() -> LLMClient:
    """The reasoning/drafting tier for the agent layer (Planner/Builder). Routed to
    ``AEO__LLM__PLANNING_PROVIDER`` (e.g. ``cloud`` for a frontier model). Falls back to the
    primary ``get_client()`` when unset or equal to the primary provider — so a hybrid
    deployment pays for frontier reasoning without changing the fast sync endpoints."""
    cfg = get_settings().llm
    if not cfg.planning_provider or cfg.planning_provider == cfg.provider:
        return get_client()
    return LLMClient(cfg.model_copy(update={"provider": cfg.planning_provider}))
