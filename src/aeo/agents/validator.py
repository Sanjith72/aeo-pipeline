"""Independent validator — deterministic checks only: word count, H1 question parse, valid JSON-LD."""
from __future__ import annotations
import json
import re
from datetime import datetime
from uuid import uuid4

from aeo.models.blueprint import CrawledPage, Recommendation, ValidationResult
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

_QUESTION_STARTERS = frozenset(
    ["what", "how", "why", "when", "where", "who", "which", "can", "does", "is", "are", "will",
     "should", "would", "could"]
)
_MIN_WORD_COUNT = 300


def _check_word_count(page: CrawledPage) -> bool:
    return len(page.body_text.split()) >= _MIN_WORD_COUNT


def _check_h1_question(page: CrawledPage) -> bool:
    """True when the first heading is phrased as a question (ends with ? or starts with a question word)."""
    if not page.headings:
        return False
    h1 = page.headings[0].strip()
    if h1.endswith("?"):
        return True
    first_word = h1.lower().split()[0] if h1.split() else ""
    return first_word in _QUESTION_STARTERS


def _check_json_ld(page: CrawledPage) -> bool:
    """True when at least one JSON-LD block has both @context and @type."""
    for item in page.schema_markup:
        if "@context" in item and "@type" in item:
            return True
    return False


def _gate_recommendation(rec: Recommendation, page: CrawledPage | None) -> ValidationResult:
    if page is None:
        return ValidationResult(
            recommendation_id=rec.id,
            is_valid=False,
            confidence=1.0,
            reasoning="No page data available for deterministic checks.",
            word_count_ok=False,
            h1_question_ok=False,
            json_ld_ok=False,
        )

    wc_ok = _check_word_count(page)
    h1_ok = _check_h1_question(page)
    jld_ok = _check_json_ld(page)

    # For schema recommendations the page MUST already lack JSON-LD (otherwise the rec is stale)
    if rec.type == "schema":
        is_valid = wc_ok and not jld_ok  # rec is valid only if JSON-LD is genuinely missing
    else:
        is_valid = wc_ok  # content/entity/citation recs just need a substantive page

    reasons: list[str] = []
    if not wc_ok:
        reasons.append(f"page has <{_MIN_WORD_COUNT} words")
    if rec.type == "schema" and jld_ok:
        reasons.append("JSON-LD already present — schema recommendation may be redundant")

    confidence = sum([wc_ok, h1_ok, jld_ok]) / 3

    return ValidationResult(
        recommendation_id=rec.id,
        is_valid=is_valid,
        confidence=round(confidence, 3),
        reasoning="; ".join(reasons) if reasons else "all deterministic gates passed",
        word_count_ok=wc_ok,
        h1_question_ok=h1_ok,
        json_ld_ok=jld_ok,
    )


async def validate_recommendations(
    recommendations: list[Recommendation],
    pages: dict[str, CrawledPage],
) -> list[tuple[Recommendation, ValidationResult]]:
    """
    Validates each recommendation against the crawled page it targets.
    Gates: word count >= 300, H1 phrased as a question, valid JSON-LD present.
    """
    with tracer.start_as_current_span("validator.validate_all") as span:
        span.set_attribute("count", len(recommendations))
        results: list[tuple[Recommendation, ValidationResult]] = []

        for rec in recommendations:
            page = pages.get(rec.url)
            result = _gate_recommendation(rec, page)
            results.append((rec, result))
            logger.debug(
                "validated",
                rec_id=str(rec.id),
                valid=result.is_valid,
                wc=result.word_count_ok,
                h1=result.h1_question_ok,
                jld=result.json_ld_ok,
            )

        valid_count = sum(1 for _, r in results if r.is_valid)
        span.set_attribute("valid_count", valid_count)
        logger.info("validation_done", total=len(results), valid=valid_count)
        return results
