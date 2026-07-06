"""InstrumentedLLM — a transparent wrapper that records per-call cost for the agent layer.

It duck-types the LLMClient surface the recommender/draft code uses (``enabled``, ``model``,
``generate``, ``generate_json``) and times + estimates each call into ``calls`` (a list of
CallTrace). An agent wraps its client in this, runs its drafting, then aggregates ``calls``
onto the agent_steps row — closing the 'LLM calls are fire-and-forget' cost-blindness risk.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from ..nlp.cost import estimate_cost, estimate_tokens


@dataclass(slots=True)
class CallTrace:
    model: str
    tokens: int
    cost_usd: float
    latency_ms: int


class InstrumentedLLM:
    """Wrap any LLMClient-shaped object; record a CallTrace per generate/generate_json call."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[CallTrace] = []

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._inner, "enabled", False))

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "unknown")

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False) -> str | None:
        return self._record(prompt, system, lambda: self._inner.generate(prompt, system, json_mode=json_mode))

    def generate_json(self, prompt: str, system: str | None = None) -> dict[str, Any] | None:
        return self._record(prompt, system, lambda: self._inner.generate_json(prompt, system))

    def embed(self, text: str) -> list[float] | None:
        """Pass-through (uncosted: embeddings are free-tier and tiny next to generation).
        Without this, wrapping a client would silently disable the semantic_search tool."""
        embedder = getattr(self._inner, "embed", None)
        return embedder(text) if embedder is not None else None

    def _record(self, prompt: str, system: str | None, call: Any) -> Any:
        t0 = time.perf_counter()
        out = call()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text_in = (system or "") + (prompt or "")
        text_out = out if isinstance(out, str) else (json.dumps(out, default=str) if out else "")
        tokens_in = estimate_tokens(text_in)
        tokens_out = estimate_tokens(text_out)
        self.calls.append(
            CallTrace(
                model=self.model,
                tokens=tokens_in + tokens_out,
                cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
                latency_ms=latency_ms,
            )
        )
        return out

    def totals(self) -> dict[str, Any]:
        """Aggregate the recorded calls for an agent_steps row."""
        return {
            "tokens": sum(c.tokens for c in self.calls),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "llm_calls": len(self.calls),
        }
