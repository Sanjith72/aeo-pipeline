"""
Perplexity client — the Independent Validator's real-world citation signal.

Asks Perplexity a page's target question and reports whether the page's own
domain shows up in the answer's citations. This is the v4 fix for *circular
validation*: instead of re-scoring against the same rubric the recommender
optimized, we check an external signal the system does not control.

Mirrors :class:`~aeo.nlp.llm.LLMClient` exactly: an OpenAI-compatible
``/chat/completions`` POST, ``enabled`` gates everything, and every failure
returns ``None`` rather than raising — a down/unkeyed Perplexity must never break
a validation run (the validator then relies on its deterministic checks alone).
Response parsing is defensive: it reads a top-level ``citations`` list when
present and otherwise scans the answer text for the domain, so it tolerates
response-shape drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..logging import get_logger
from ..settings import PerplexityCfg, get_settings

log = get_logger(__name__)


@dataclass(slots=True)
class CitationProbe:
    """Outcome of one Perplexity citation query."""

    question: str
    cited: bool
    citations: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)  # which citations matched our domain
    answer: str = ""


def _domain(url: str) -> str:
    host = (urlsplit(url).netloc or url).lower()
    return host[4:] if host.startswith("www.") else host


class PerplexityClient:
    """Thin facade over Perplexity's chat-completions API. Returns ``None`` on
    failure or when disabled — never raises into the caller."""

    def __init__(self, cfg: PerplexityCfg) -> None:
        self._cfg = cfg

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled and bool(self._cfg.api_key)

    @property
    def model(self) -> str:
        return self._cfg.model

    def cited(self, question: str, *, target_url: str) -> CitationProbe | None:
        """Query ``question`` and report whether ``target_url``'s domain is cited.

        Returns ``None`` when disabled or on any transport/parse failure, so the
        validator can distinguish "not run" from a real "not cited" result."""
        if not self.enabled or not question.strip():
            return None
        from ..crawl.transport import sync_transport

        payload: dict[str, Any] = {
            "model": self._cfg.model,
            "messages": [{"role": "user", "content": question}],
        }
        headers = {"Authorization": f"Bearer {self._cfg.api_key or ''}"}
        try:
            with httpx.Client(timeout=self._cfg.timeout_sec, transport=sync_transport()) as client:
                resp = client.post(
                    f"{self._cfg.base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # never let the citation test break validation
            log.warning("perplexity_query_failed", model=self._cfg.model, error=str(exc))
            return None

        return self._probe(question, target_url, data)

    @staticmethod
    def _probe(question: str, target_url: str, data: dict[str, Any]) -> CitationProbe:
        citations = [str(c) for c in (data.get("citations") or []) if c]
        answer = ""
        try:
            answer = str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            answer = ""

        domain = _domain(target_url)
        matched = [c for c in citations if domain and domain in c.lower()]
        # Fall back to scanning the answer text when no structured citations matched.
        cited = bool(matched) or (bool(domain) and domain in answer.lower())
        return CitationProbe(
            question=question,
            cited=cited,
            citations=citations,
            matched=matched,
            answer=answer,
        )


@lru_cache(maxsize=1)
def get_perplexity_client() -> PerplexityClient:
    return PerplexityClient(get_settings().perplexity)
