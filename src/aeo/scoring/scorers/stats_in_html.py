"""
Criterion 3 — Stats in HTML.

Deterministic count of distinct concrete numeric claims, mapped to tiers
{1:0, 2:1, 3:3, 4:6, 5:10}. When the LLM is enabled it acts as a
*disqualifier*: it re-counts how many candidates are genuine statistics
(not dates, versions, prices) and the lower count is used.
"""

from __future__ import annotations

from typing import Any

from ...nlp.llm import LLMClient
from ...storage.models import CriterionScore
from ..result import ScoreContext, tier_from_thresholds


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("stats_in_html")
    tiers = crit.cfg.get("tiers", {1: 0, 2: 1, 3: 3, 4: 6, 5: 10})

    stats = ctx.bundle.get("stats", {}) or {}
    raw_count = int(stats.get("count", 0) or 0)
    by_kind = stats.get("by_kind", {})
    hits = stats.get("hits", [])

    effective = raw_count
    scored_by = "deterministic"
    llm_kept: int | None = None

    if ctx.llm is not None and ctx.llm.enabled and raw_count > 0:
        kept = _llm_disqualify(ctx.llm, hits)
        if kept is not None:
            effective = kept
            llm_kept = kept
            scored_by = "hybrid"

    value = tier_from_thresholds(effective, tiers)

    return CriterionScore(
        name="stats_in_html",
        value=value,
        evidence={
            "raw_count": raw_count,
            "effective_count": effective,
            "llm_genuine_count": llm_kept,
            "by_kind": by_kind,
            "samples": [h.get("value") for h in hits[:10]],
        },
        notes=f"{effective} distinct statistic(s)",
        scored_by=scored_by,
    )


def _llm_disqualify(llm: LLMClient, hits: list[dict[str, Any]]) -> int | None:
    sample = hits[:25]
    lines = "\n".join(
        f"{i + 1}. {h.get('value', '')}  (context: {h.get('context', '')[:80]})"
        for i, h in enumerate(sample)
    )
    prompt = (
        "Below are candidate numeric values extracted from a web page. Count how "
        "many are GENUINE quantitative statistics or factual data points "
        "(e.g. '43% of breaches', '2.5x faster', '$4.2M average cost'). Do NOT "
        "count calendar dates, years, phone numbers, page/section numbers, "
        "product prices, or software version numbers.\n\n"
        f"{lines}\n\n"
        'Respond with ONLY JSON: {"genuine": <integer>}'
    )
    obj = llm.generate_json(prompt)
    if obj and isinstance(obj.get("genuine"), (int, float)):
        return max(0, min(len(hits), int(obj["genuine"])))
    return None
