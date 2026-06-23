"""Critic agent — a model-isolated quality + safety gate over staged drafts.

For each drafted page task, three checks run, each deterministic-first (no network unless a
client is supplied + enabled):

  1. INDEPENDENT (validation.draft_check.validate_page_draft): the non-circular Independent
     Validator (lead-answer liftable, H1-is-a-question, valid JSON-LD) + citation-signal check.
  2. ADVERSARIAL (validation.adversarial.adversarial_audit): a distinct 'refute this' persona
     (isolation is in the prompt, not the vendor) + deterministic citation-hallucination check.
  3. CLAIM/COMPLIANCE (claim_audit): extract specific factual/statistical claims that a
     publisher must verify before shipping (the merged Safety/Compliance auditor).

The Critic ANNOTATES each task with a verdict under ``task['critic']`` and flags it for human
attention; it NEVER publishes and NEVER auto-rejects. The human approval gate (2A
/api/agent/run/{id}/approve|reject) remains the sole authority. A draft 'passes' only when the
independent checks pass AND the adversarial auditor did not refute it.
"""

from __future__ import annotations

import re
from typing import Any

from ..validation.adversarial import adversarial_audit
from ..validation.draft_check import validate_page_draft

# Deterministic floor for the claim auditor: stat/superlative/guarantee phrasing a publisher
# should verify. Used when no LLM is available (or as the cheap default).
_CLAIM_RE = re.compile(
    r"\b(\d+(?:\.\d+)?\s?%|no\.?\s?1\b|#\s?1\b|\bguarantee(?:d|s)?\b|\bcertified\b|"
    r"\bleading\b|\bbest[- ]in[- ]class\b|\b(?:fastest|cheapest|largest|#1)\b)",
    re.I,
)
_MAX_CLAIM_TEXT = 4000

_CLAIM_SYSTEM = (
    "You are a compliance reviewer for marketing copy. Extract every SPECIFIC factual or "
    "statistical claim in the text that a publisher would need to verify before publishing — "
    "numbers, percentages, superlatives (e.g. 'the leading', '#1'), named certifications, and "
    "guarantees. Do NOT invent claims that are not in the text. Reply with JSON only: "
    '{"claims": ["...", "..."]}.'
)


def claim_audit(text: str, *, llm: Any = None) -> dict[str, Any]:
    """Flag verifiable factual/statistical claims in ``text``. Frontier extraction when a model
    is enabled, a regex floor otherwise. Returns ``{flagged, claims, source}``. Never raises."""
    snippet = (text or "")[:_MAX_CLAIM_TEXT]
    if llm is not None and getattr(llm, "enabled", False):
        try:
            data = llm.generate_json(f"Text to review:\n{snippet}", _CLAIM_SYSTEM)
        except Exception:  # never let the auditor break a run
            data = None
        if isinstance(data, dict):
            claims = [str(c).strip() for c in (data.get("claims") or []) if str(c).strip()][:20]
            return {"flagged": bool(claims), "claims": claims, "source": "llm"}
    hits = sorted({m.group(0).strip() for m in _CLAIM_RE.finditer(snippet)})
    return {"flagged": bool(hits), "claims": hits, "source": "deterministic"}


def review_drafts(
    graph: dict[str, Any],
    *,
    llm: Any = None,
    origin: str | None = None,
    verify_citations: bool = False,
    adversarial_max_attempts: int = 3,
) -> dict[str, Any]:
    """Annotate every drafted task with a Critic verdict. Mutates the graph in place; returns it.
    ``llm`` may be an InstrumentedLLM so the caller can aggregate cost afterward."""
    for task in graph.get("tasks", []):
        draft = task.get("draft")
        if not draft:
            continue
        slug = task.get("slug") or ""
        url = f"{origin}{slug}" if origin else None
        body = str(draft.get("body_markdown", ""))

        independent = validate_page_draft(draft, url=url, verify_reachability=verify_citations)
        adversarial = adversarial_audit(
            body, llm=llm, verify_reachability=verify_citations, max_attempts=adversarial_max_attempts
        )
        claims = claim_audit(body, llm=llm)

        passed = bool(independent["passed"]) and adversarial.passed
        needs_review = (not passed) or claims["flagged"]
        task["critic"] = {
            "passed": passed,
            "independent_passed": bool(independent["passed"]),
            "independent": independent,
            "adversarial": adversarial.to_detail(),
            "claims_flagged": claims["flagged"],
            "claims": claims["claims"],
            "needs_review": needs_review,
        }
        task["status"] = "reviewed" if not needs_review else "flagged"
    return graph
