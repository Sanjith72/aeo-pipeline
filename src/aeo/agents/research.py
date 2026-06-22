"""Research agent — discover and live-verify a brief's competitors.

A thin agent wrapper over reference.competitor_discovery.discover_competitors: it proposes
competitors via the LLM and verifies each domain with a concurrent HEAD probe, returning only
reachable ones. Deterministic-first: no LLM (or any failure) yields an empty result, never an
exception. The verified domains are folded back into the brief so the Planner benchmarks
against real peers, and surfaced to the human in the staged run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..nlp.llm import LLMClient
from ..reference.competitor_discovery import discover_competitors


def research_competitors(
    brief: dict[str, Any],
    *,
    llm: LLMClient | None = None,
    head_check: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Return ``{'competitors': [...], 'dropped': [...], 'relaxed': bool}`` for a brief dict.

    ``head_check`` is injectable for tests (defaults to the live force-IPv4 HEAD probe)."""
    name = brief.get("name") or brief.get("domain") or "site"
    result = discover_competitors(
        name,
        brief.get("domain") or "",
        topic=brief.get("topic"),
        location=brief.get("location"),
        services=list(brief.get("services") or []),
        llm=llm,
        head_check=head_check,
    )
    return {
        "competitors": [{"name": c.name, "domain": c.domain} for c in result.verified],
        "dropped": [{"name": c.name, "domain": c.domain} for c in result.dropped],
        "relaxed": result.relaxed,
    }
