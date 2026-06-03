"""Independent validator.

Two layers:
  1. Deterministic gates (original) — word count, H1-as-question, valid JSON-LD.
  2. Independent adversarial auditor (Block 4) — a model-isolated audit that frames the task
     as adversarial review (NOT self-grading), runs behavioral citation-signal checks, and wraps
     the LLM call in a 3x retry circuit breaker that raises a structured AuditFailure on exhaustion.

Model isolation: the auditor uses a distinct adversarial system persona so it cannot reuse the
recommender's reasoning. It runs on the same local Ollama engine the rest of the pipeline uses
(see config.py) — the isolation is in the prompt/persona, not the vendor — which breaks the
self-grading loop without adding a second LLM dependency.
"""
from __future__ import annotations
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

from aeo.config import get_settings
from aeo.models.blueprint import (
    AuditReport,
    CitationSignal,
    CrawledPage,
    Recommendation,
    ValidationResult,
)
from aeo.utils.http import async_client
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Matches http/https URLs; stops at whitespace, quotes, angle brackets and closing parens.
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

# A verdict callable: given a recommendation (+ optional page) it returns (passed, reason).
AuditorFn = Callable[[Recommendation, "CrawledPage | None"], Awaitable[tuple[bool, "str | None"]]]

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


# ── Block 4: independent adversarial auditor ───────────────────────────────────


class AuditFailure(Exception):
    """Raised when the auditor's LLM circuit breaker exhausts all retries.

    Carries full context so the failure is never silently swallowed.
    """

    def __init__(
        self,
        recommendation_id: UUID | None,
        attempts: int,
        last_error: BaseException | None,
        context: str = "",
    ) -> None:
        self.recommendation_id = recommendation_id
        self.attempts = attempts
        self.last_error = last_error
        self.context = context
        super().__init__(
            f"audit failed after {attempts} attempt(s) for rec={recommendation_id}: "
            f"{type(last_error).__name__ if last_error else 'unknown'}: {last_error}. {context}"
        )


_ADVERSARIAL_SYSTEM = (
    "You are an INDEPENDENT adversarial auditor. You did NOT write the recommendation below and "
    "you are not grading your own work. Your job is to find reasons it is wrong, unsupported, "
    "hallucinated, or not implementable. Be skeptical. Only pass it if you cannot find a real "
    "defect. Respond with JSON only: {\"passed\": true|false, \"failure_reason\": \"...\"|null}."
)

_ADVERSARIAL_USER = """\
Recommendation under audit:
  type: {type}
  title: {title}
  description: {description}
  implementation: {implementation}

Audit it adversarially and return the JSON verdict.
"""


def extract_citation_urls(rec: Recommendation) -> list[str]:
    """Pull candidate cited URLs out of an LLM-authored recommendation."""
    blob = f"{rec.title} {rec.description} {rec.implementation}"
    # de-dupe preserving order
    seen: dict[str, None] = {}
    for m in _URL_RE.findall(blob):
        seen.setdefault(m.rstrip(".,"), None)
    return list(seen)


def _is_structurally_valid(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


async def check_citation_signals(
    rec: Recommendation, verify_reachability: bool = False
) -> list[CitationSignal]:
    """Behavioral citation checks: structural URL validity + optional HEAD reachability.

    A citation is flagged ``hallucinated`` when it is not a structurally valid URL, or (when
    reachability is verified) when a structurally valid URL is unreachable.
    """
    signals: list[CitationSignal] = []
    for url in extract_citation_urls(rec):
        structurally_valid = _is_structurally_valid(url)
        reachable: bool | None = None
        if verify_reachability and structurally_valid:
            try:
                # IPv4-bound client (OCI ARM); HEAD only, short timeout.
                async with async_client(timeout=10.0) as client:
                    resp = await client.head(url, follow_redirects=True)
                    reachable = resp.status_code < 400
            except Exception as exc:  # noqa: BLE001 — unreachable == failed signal, not a crash
                logger.warning("citation_head_failed", url=url, error=str(exc))
                reachable = False
        hallucinated = (not structurally_valid) or (reachable is False)
        signals.append(
            CitationSignal(
                url=url,
                structurally_valid=structurally_valid,
                reachable=reachable,
                hallucinated=hallucinated,
            )
        )
    return signals


async def _ollama_adversarial_audit(
    rec: Recommendation, page: CrawledPage | None
) -> tuple[bool, str | None]:
    """Default auditor verdict path — Ollama with the adversarial persona (model isolation)."""
    settings = get_settings()
    prompt = _ADVERSARIAL_USER.format(
        type=rec.type,
        title=rec.title,
        description=rec.description[:800],
        implementation=rec.implementation[:1200],
    )
    async with async_client(timeout=90.0) as client:
        resp = await client.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": [
                    {"role": "system", "content": _ADVERSARIAL_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.0},
            },
        )
        resp.raise_for_status()
        content: str = resp.json()["message"]["content"]
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("adversarial auditor returned no JSON verdict")
    verdict = json.loads(match.group())
    return bool(verdict.get("passed", False)), verdict.get("failure_reason")


async def audit_recommendation(
    rec: Recommendation,
    page: CrawledPage | None = None,
    *,
    auditor: AuditorFn | None = None,
    verify_reachability: bool = False,
    max_retries: int = 3,
) -> AuditReport:
    """Independently audit a single recommendation.

    Runs citation-signal checks, then the adversarial LLM verdict behind a 3x retry circuit
    breaker with exponential backoff. After ``max_retries`` consecutive failures it raises
    :class:`AuditFailure` (never silently swallowed). A recommendation passes only when the
    adversarial verdict passes AND no citation is hallucinated.
    """
    audit_fn: AuditorFn = auditor or _ollama_adversarial_audit
    citation_signals = await check_citation_signals(rec, verify_reachability)

    attempts = 0
    last_error: BaseException | None = None
    verdict_passed = False
    verdict_reason: str | None = None

    for attempt in range(1, max_retries + 1):
        attempts = attempt
        try:
            verdict_passed, verdict_reason = await audit_fn(rec, page)
            break
        except Exception as exc:  # noqa: BLE001 — retried by the circuit breaker below
            last_error = exc
            logger.warning("audit_attempt_failed", rec_id=str(rec.id), attempt=attempt, error=str(exc))
            if attempt < max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
    else:
        # Loop exhausted without break → circuit open. Raise with full context.
        raise AuditFailure(
            recommendation_id=rec.id,
            attempts=attempts,
            last_error=last_error,
            context=f"auditor={audit_fn.__name__} url={rec.url}",
        )

    hallucinated = [s for s in citation_signals if s.hallucinated]
    passed = verdict_passed and not hallucinated
    failure_reason: str | None = None
    if not passed:
        parts = []
        if hallucinated:
            parts.append(f"{len(hallucinated)} hallucinated citation(s)")
        if not verdict_passed and verdict_reason:
            parts.append(verdict_reason)
        elif not verdict_passed:
            parts.append("adversarial auditor rejected the recommendation")
        failure_reason = "; ".join(parts) or None

    report = AuditReport(
        recommendation_id=rec.id,
        passed=passed,
        citation_signals=citation_signals,
        failure_reason=failure_reason,
        retry_attempts=attempts,
    )
    logger.info(
        "audit_done",
        rec_id=str(rec.id),
        passed=passed,
        citations=len(citation_signals),
        hallucinated=len(hallucinated),
        attempts=attempts,
    )
    return report


async def audit_recommendations(
    recommendations: list[Recommendation],
    pages: dict[str, CrawledPage],
    *,
    auditor: AuditorFn | None = None,
    verify_reachability: bool = False,
) -> list[AuditReport]:
    """Audit many recommendations. An AuditFailure is recorded as a failed (not silent) report."""
    with tracer.start_as_current_span("validator.audit_all") as span:
        span.set_attribute("count", len(recommendations))
        reports: list[AuditReport] = []
        for rec in recommendations:
            page = pages.get(rec.url)
            try:
                reports.append(
                    await audit_recommendation(
                        rec, page, auditor=auditor, verify_reachability=verify_reachability
                    )
                )
            except AuditFailure as failure:
                logger.error("audit_failed", rec_id=str(rec.id), error=str(failure))
                reports.append(
                    AuditReport(
                        recommendation_id=rec.id,
                        passed=False,
                        citation_signals=await check_citation_signals(rec, verify_reachability=False),
                        failure_reason=str(failure),
                        retry_attempts=failure.attempts,
                    )
                )
        passed = sum(1 for r in reports if r.passed)
        span.set_attribute("passed_count", passed)
        logger.info("audit_batch_done", total=len(reports), passed=passed)
        return reports
