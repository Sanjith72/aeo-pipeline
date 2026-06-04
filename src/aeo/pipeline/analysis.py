"""
Per-page analysis wiring — the back half of the pipeline.

After a page is crawled, extracted, and scored, this runs the remaining steps as
one isolated unit:

    Gap analysis -> Validate (recommend + simulate + retry <=3) -> Report

``analyze_page`` ties the already-built blocks together for a single page: it
classifies query intent, picks the best competitor page for that intent, runs the
Dual-Layer Gap Analysis, validates the proposed fixes on a synthetic page, and
assembles the per-page report. Each step is wrapped in ``trace_step`` for
observability; the orchestrator wraps the whole call in the Error Sink so one bad
page never kills a run.

``build_competitor_pool`` turns raw competitor score rows into the
:class:`CompetitorPage` candidates the gap analysis compares against, classifying
each competitor's intent from its URL (the lightweight heuristic) so no
competitor bundle needs reloading.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from ..nlp.llm import LLMClient
from ..obs import trace_step
from ..processor import (
    CompetitorPage,
    GapResult,
    analyze_gap,
    persist_gap,
    select_competitor,
)
from ..reference import Reference, load_reference
from ..report import PageReport, build_report, persist_report
from ..scoring.rubric import Rubric, load_rubric
from ..scoring.scorers import SCORERS
from ..storage.models import ExtractionBundle, PageScore
from ..validation import (
    STATUS_COULD_NOT_IMPROVE,
    STATUS_IMPROVED,
    AdversarialVerdict,
    IndependentVerdict,
    ValidationOutcome,
    adversarial_audit,
    validate_independent,
    validate_page,
)

# Map each rubric criterion to its rubric_scores_v2 tier column.
_TIER_COLUMNS = {name: f"{name}_score" for name in SCORERS}


@dataclass(slots=True)
class AnalysisResult:
    page_id: int
    run_id: int
    intent: str | None
    gap: GapResult | None = None
    validation: ValidationOutcome | None = None
    report: PageReport | None = None
    independent: IndependentVerdict | None = None
    adversarial: AdversarialVerdict | None = None


def build_competitor_pool(rows: list[dict[str, Any]], reference: Reference) -> list[CompetitorPage]:
    """Turn competitor score rows (rubric_scores_v2 + url) into gap-analysis
    candidates. Intent is classified from the URL alone (the documented
    lightweight heuristic), so no competitor extraction bundle is reloaded."""
    pool: list[CompetitorPage] = []
    for row in rows:
        tiers = {
            name: int(row[col])
            for name, col in _TIER_COLUMNS.items()
            if row.get(col) is not None
        }
        if not tiers:
            continue
        pool.append(
            CompetitorPage(
                page_id=int(row["page_id"]),
                intent=reference.classify_intent(row["url"]),
                total=int(row.get("total_score") or 0),
                tiers=tiers,
            )
        )
    return pool


def analyze_page(
    *,
    bundle: ExtractionBundle,
    score: PageScore,
    url: str,
    reference: Reference | None = None,
    rubric: Rubric | None = None,
    llm: LLMClient | None = None,
    competitors: list[CompetitorPage] | None = None,
    page_type: str | None = None,
    intent: str | None = None,
    persist: bool = True,
    trace: bool = True,
    perplexity=None,
    independent: bool = False,
    adversarial: bool = False,
    verify_citations: bool = False,
    question: str | None = None,
) -> AnalysisResult:
    """Run Gap -> Validate -> [Independent-Validate] -> Report for one scored page.

    The v3 ``validate_page`` is retained as the *edit-efficacy* gate (does the
    proposed fix raise the deterministic score?). When ``independent`` is on, the
    v4 Independent Validator additionally checks non-circular signals (liftable
    TL;DR, H1-as-question, valid JSON-LD) and, if a Perplexity client is enabled,
    the real-world citation test — fixing v3's circular validation. The citation
    outcome is logged for the validated-wins loop."""
    reference = reference or load_reference()
    rubric = rubric or load_rubric()
    page_id, run_id = score.page_id, score.run_id

    if intent is None:
        intent = reference.classify_intent(url, _headings(bundle))
    competitor = select_competitor(competitors or [], intent)

    with _step(trace, "processor", run_id, page_id, "gap"):
        gap = analyze_gap(
            score, reference=reference, rubric=rubric, competitor=competitor, intent=intent
        )
        if persist:
            persist_gap(gap)

    model = llm.model if (llm is not None and llm.enabled) else None
    with _step(trace, "validator", run_id, page_id, "validate", model=model):
        validation = validate_page(
            bundle, gap, url=url, reference=reference, rubric=rubric,
            llm=llm, page_type=page_type, persist=persist,
        )

    independent_verdict: IndependentVerdict | None = None
    if independent:
        with _step(trace, "validator", run_id, page_id, "independent"):
            independent_verdict = validate_independent(
                bundle, url=url, question=question, perplexity=perplexity
            )
            if persist and independent_verdict.citation is not None and independent_verdict.citation.available:
                _record_citation(page_id, run_id, url, independent_verdict)

    # Adversarial audit (ported): a model-isolated skeptic over the proposed edits +
    # deterministic citation-hallucination checks. Optional; degrades to the
    # deterministic citation layer when the LLM is off, and never gates the pipeline.
    adversarial_verdict: AdversarialVerdict | None = None
    if adversarial and validation.recommendations:
        with _step(trace, "validator", run_id, page_id, "adversarial", model=model):
            adversarial_verdict = adversarial_audit(
                _audit_text(validation.recommendations),
                llm=llm, verify_reachability=verify_citations,
            )

    with _step(trace, "reporter", run_id, page_id, "report"):
        report = build_report(
            url=url, score=score, gap=gap, validation=validation,
            page_type=page_type, intent=intent, independent=independent_verdict,
            adversarial=adversarial_verdict,
        )
        if persist:
            persist_report(report)

    return AnalysisResult(
        page_id=page_id, run_id=run_id, intent=intent,
        gap=gap, validation=validation, report=report,
        independent=independent_verdict, adversarial=adversarial_verdict,
    )


def _record_citation(page_id: int, run_id: int, url: str, verdict: IndependentVerdict) -> None:
    """Log the Perplexity citation outcome for the validated-wins feedback loop."""
    cit = verdict.citation
    if cit is None:
        return
    from ..storage.repos import feedback as feedback_repo

    feedback_repo.record_citation(
        page_id=page_id, run_id=run_id, url=url,
        question=(cit.question or verdict.question or ""),
        cited=cit.cited,
        evidence={"matched": cit.matched, "citations": cit.citations},
    )


def is_improved(result: AnalysisResult) -> bool:
    return result.validation is not None and result.validation.status == STATUS_IMPROVED


def is_could_not_improve(result: AnalysisResult) -> bool:
    return result.validation is not None and result.validation.status == STATUS_COULD_NOT_IMPROVE


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _step(trace: bool, agent: str, run_id: int, page_id: int, step: str, *, model: str | None = None):
    """trace_step when tracing is on; a no-op context (DB-free) when off."""
    if not trace:
        return nullcontext()
    return trace_step(agent, run_id=run_id, page_id=page_id, step=step, model=model)


def _headings(bundle: ExtractionBundle) -> list[str]:
    h = bundle.get("headings", {}) or {}
    by_level = h.get("by_level", {}) or {}
    return [*(by_level.get("h2", []) or []), *(by_level.get("h3", []) or [])]


def _audit_text(recs: list) -> str:
    """Flatten the proposed edits into text for the adversarial audit (title +
    rationale + the concrete edit payload), so cited URLs and claims are visible."""
    parts: list[str] = []
    for r in recs:
        payload_text = " ".join(str(v) for v in (r.payload or {}).values())
        parts.append("\n".join(p for p in (r.title, r.rationale, payload_text) if p))
    return "\n\n".join(parts)
