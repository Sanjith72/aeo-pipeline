"""
Criterion 2 — Q&A Blocks.

Counts *real* question→answer pairs (a question heading followed by a
substantive paragraph; the qa_blocks extractor already enforces a minimum
answer length). Explicit FAQPage schema earns a bonus tier.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext, clamp_tier


def score(ctx: ScoreContext) -> CriterionScore:
    qa = ctx.bundle.get("qa_blocks", {}) or {}
    schema = ctx.bundle.get("schema_jsonld", {}) or {}

    pairs = int(qa.get("pair_count", 0) or 0)
    q_headings = int(qa.get("question_heading_count", 0) or 0)
    has_faq = "FAQPage" in set(schema.get("types", []))

    if pairs == 0:
        value = 1
    elif pairs == 1:
        value = 2
    elif pairs == 2:
        value = 3
    elif pairs <= 4:
        value = 4
    else:
        value = 5

    if has_faq:
        value = clamp_tier(value + 1)

    notes = []
    if pairs == 0 and q_headings:
        notes.append(f"{q_headings} question heading(s) but no substantive answers")
    if has_faq:
        notes.append("FAQPage schema present")

    samples = [p.get("question", "") for p in qa.get("qa_pairs", [])[:5]]

    return CriterionScore(
        name="qa_blocks",
        value=value,
        evidence={
            "pair_count": pairs,
            "question_heading_count": q_headings,
            "has_faqpage_schema": has_faq,
            "sample_questions": samples,
        },
        notes="; ".join(notes) or f"{pairs} Q&A pair(s)",
    )
