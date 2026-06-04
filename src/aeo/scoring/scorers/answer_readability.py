"""
Criterion 10 (v3) — Answer Readability.

Answer engines lift concise, well-segmented passages. This rewards content that
is *quotable*: a reasonable Flesch reading ease, sentences short enough to
extract, and passages chunked into digestible sizes. Deterministic — wraps the
``readability`` and ``chunker`` extractors.

Base tier from Flesch reading ease, then:
  - -1 if the average sentence is very long (hard to extract a clean answer);
  - +1 if the page is segmented into several passages (good for retrieval),
    -1 if it is a single monolithic block.
Pages with almost no text have no readable answer to assess → floor at 1.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext, clamp_tier, tier_from_thresholds

_DEFAULT_FLESCH_TIERS = {1: 0, 2: 20, 3: 30, 4: 45, 5: 55}


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("answer_readability")
    flesch_tiers = crit.cfg.get("flesch_tiers", _DEFAULT_FLESCH_TIERS)
    max_sent = float(crit.cfg.get("max_avg_sentence_len", 28))
    min_chunks = int(crit.cfg.get("min_chunks_for_credit", 3))
    min_words = int(crit.cfg.get("min_word_count", 50))

    read = ctx.bundle.get("readability", {}) or {}
    chunk = ctx.bundle.get("chunker", {}) or {}

    wc = int(read.get("word_count", 0) or 0)
    flesch = read.get("flesch_reading_ease")

    if wc < min_words or flesch is None:
        return CriterionScore(
            name="answer_readability",
            value=1,
            evidence={"word_count": wc, "reason": "insufficient text to assess"},
            notes="too little readable content",
            scored_by="deterministic",
        )

    base = tier_from_thresholds(float(flesch), flesch_tiers)
    value = base
    adjustments: list[str] = []

    avg_sent = read.get("avg_sentence_length")
    if avg_sent is not None and float(avg_sent) > max_sent:
        value -= 1
        adjustments.append(f"long sentences ({float(avg_sent):.0f} words avg)")

    chunk_count = int(chunk.get("chunk_count", 0) or 0)
    if chunk_count >= min_chunks:
        value += 1
        adjustments.append(f"{chunk_count} passages")
    elif chunk_count <= 1:
        value -= 1
        adjustments.append("monolithic (poorly segmented)")

    value = clamp_tier(value)

    return CriterionScore(
        name="answer_readability",
        value=value,
        evidence={
            "word_count": wc,
            "flesch_reading_ease": flesch,
            "avg_sentence_length": avg_sent,
            "chunk_count": chunk_count,
            "base_tier": base,
            "adjustments": adjustments,
        },
        notes="; ".join(adjustments) or f"Flesch {float(flesch):.0f}",
        scored_by="deterministic",
    )
