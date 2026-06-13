"""
Per-page report assembly — the system's final deliverable.

``build_report`` folds everything the pipeline learned about one page into a
single structured record:

  * **overview** — identity + headline score (total/max, percentage, priority);
  * **scores** — the 10 criterion tiers, worst-first (this is a remediation doc);
  * **gap** — the Dual-Layer Gap Analysis (best-practice + competitor) and the
    prioritized deficiency list;
  * **recommendations** — the concrete proposed edits, ordered by gap priority;
  * **validation** — did the simulated fix raise the score (before -> after)?
  * **human_review** — the review status + why, since "Human Review" is a DB
    flag and a report section, not a UI.

Pure and deterministic: it transforms already-computed objects into a
``PageReport`` (summary text + ``sections`` JSONB + ``review_status``).
Persistence is a separate thin helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..processor import GapResult
from ..recommender import Recommendation
from ..storage.models import PageScore
from ..validation import (
    STATUS_COULD_NOT_IMPROVE,
    STATUS_IMPROVED,
    STATUS_NO_ACTION,
    AdversarialVerdict,
    IndependentVerdict,
    ValidationOutcome,
)

# page_reports.review_status CHECK domain.
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"
REVIEW_COULD_NOT_IMPROVE = "could_not_improve"

# Validation outcome -> Human-Review routing.
_REVIEW_FOR_STATUS = {
    STATUS_IMPROVED: REVIEW_PENDING,            # validated; a human approves applying it
    STATUS_COULD_NOT_IMPROVE: REVIEW_COULD_NOT_IMPROVE,
    STATUS_NO_ACTION: REVIEW_APPROVED,          # nothing to fix
}


@dataclass(slots=True)
class PageReport:
    page_id: int
    run_id: int
    url: str
    summary: str
    sections: dict[str, Any]
    review_status: str = REVIEW_PENDING


def build_report(
    *,
    url: str,
    score: PageScore,
    gap: GapResult | None = None,
    validation: ValidationOutcome | None = None,
    recommendations: list[Recommendation] | None = None,
    page_type: str | None = None,
    intent: str | None = None,
    independent: IndependentVerdict | None = None,
    adversarial: AdversarialVerdict | None = None,
) -> PageReport:
    """Assemble the per-page AEO/SEO report from the pipeline's outputs."""
    recs = recommendations if recommendations is not None else (
        validation.recommendations if validation is not None else []
    )
    recs = _order_by_gap_priority(recs, gap)

    review_status = (
        _REVIEW_FOR_STATUS.get(validation.status, REVIEW_PENDING)
        if validation is not None
        else REVIEW_PENDING
    )

    sections: dict[str, Any] = {
        "overview": _overview(url, score, page_type, intent),
        "scores": _scores(score),
        "gap": _gap(gap),
        "recommendations": [_rec(r) for r in recs],
        "validation": _validation(validation, score.max_possible),
        "human_review": _human_review(review_status, validation),
    }
    # v4: the Independent Validator's non-circular verdict (deterministic signals
    # + optional Perplexity citation). Only present when independent validation ran.
    if independent is not None:
        sections["independent_validation"] = independent.to_detail()
    # v4+: the adversarial auditor's verdict (model-isolated skeptic + citation-
    # hallucination checks). Only present when the adversarial audit ran.
    if adversarial is not None:
        sections["adversarial_audit"] = adversarial.to_detail()
    summary = _summary(url, score, gap, validation, len(recs), review_status)

    return PageReport(
        page_id=score.page_id,
        run_id=score.run_id,
        url=url,
        summary=summary,
        sections=sections,
        review_status=review_status,
    )


def persist_report(report: PageReport) -> int:
    """Write the report to page_reports (upsert on page_id/run_id). Returns its id."""
    from ..storage.repos import reports as reports_repo

    return reports_repo.put(
        report.page_id,
        report.run_id,
        report.summary,
        report.sections,
        review_status=report.review_status,
    )


# ---------------------------------------------------------------------------
# section builders
# ---------------------------------------------------------------------------


def _pct(total: int, max_possible: int) -> float:
    return round(total / max_possible * 100, 1) if max_possible else 0.0


def _overview(url: str, score: PageScore, page_type: str | None, intent: str | None) -> dict[str, Any]:
    return {
        "url": url,
        "page_type": page_type,
        "intent": intent,
        "total": score.total,
        "max_possible": score.max_possible,
        "score_pct": _pct(score.total, score.max_possible),
        "priority_tier": score.priority_tier,
        "rubric_version": score.rubric_version,
    }


def _scores(score: PageScore) -> list[dict[str, Any]]:
    """Criterion tiers, weakest first — the remediation order."""
    rows = [
        {
            "criterion": name,
            "value": crit.value,
            "scored_by": crit.scored_by,
            "notes": crit.notes,
        }
        for name, crit in score.criteria.items()
    ]
    rows.sort(key=lambda r: (r["value"], r["criterion"]))
    return rows


def _gap(gap: GapResult | None) -> dict[str, Any] | None:
    if gap is None:
        return None
    return {
        "bestpractice_gap": gap.bestpractice_gap,
        "competitor_gap": gap.competitor_gap,
        "overall_gap": gap.overall_gap,
        "competitor_available": gap.competitor_gap is not None,
        "intent": gap.intent,
        "deficiencies": [
            {
                "criterion": g.criterion,
                "actual": g.actual,
                "target": g.target,
                "bestpractice_gap": g.bestpractice_gap,
                "competitor": g.competitor,
                "competitor_gap": g.competitor_gap,
                "priority": g.priority,
            }
            for g in gap.criterion_gaps
        ],
    }


def _rec(rec: Recommendation) -> dict[str, Any]:
    detail = {k: v for k, v in rec.payload.items()}
    return {
        "criterion": rec.criterion,
        "type": rec.rec_type,
        "title": rec.title,
        "rationale": rec.rationale,
        "source": rec.scored_by,
        # #12 — the three plain-language fields the in-app checklist renders.
        "current_state": rec.effective_current_state(),
        "action_required": rec.effective_action_required(),
        "how_to": rec.effective_how_to(),
        "detail": detail,
    }


def _validation(validation: ValidationOutcome | None, max_possible: int) -> dict[str, Any] | None:
    if validation is None:
        return None
    return {
        "status": validation.status,
        "improved": validation.improved,
        "attempts": validation.attempts,
        "score_before": validation.score_before,
        "score_after": validation.score_after,
        "delta": validation.score_after - validation.score_before,
        "max_possible": max_possible,
    }


def _human_review(review_status: str, validation: ValidationOutcome | None) -> dict[str, Any]:
    attempts = validation.attempts if validation is not None else 0
    delta = (validation.score_after - validation.score_before) if validation is not None else 0
    if review_status == REVIEW_COULD_NOT_IMPROVE:
        reason = (
            f"Automatic fixes could not raise the score after {attempts} attempt(s); "
            "needs manual review."
        )
        action_required = True
    elif review_status == REVIEW_APPROVED:
        reason = "No deficiencies found; no action required."
        action_required = False
    else:  # pending
        reason = (
            f"Recommendations validated (simulated +{delta}); awaiting human approval to apply."
            if validation is not None and validation.improved
            else "Recommendations proposed; awaiting human review."
        )
        action_required = True
    return {"status": review_status, "reason": reason, "action_required": action_required}


def _summary(
    url: str,
    score: PageScore,
    gap: GapResult | None,
    validation: ValidationOutcome | None,
    n_recs: int,
    review_status: str,
) -> str:
    pct = _pct(score.total, score.max_possible)
    n_def = len(gap.criterion_gaps) if gap is not None else 0
    parts = [
        f"{url} scored {score.total}/{score.max_possible} ({pct}%, {score.priority_tier} priority).",
        f"{n_def} deficienc{'y' if n_def == 1 else 'ies'}, {n_recs} recommendation(s).",
    ]
    if validation is not None:
        if validation.improved:
            parts.append(
                f"Validated: simulated score rises to {validation.score_after}/{score.max_possible} "
                f"(+{validation.score_after - validation.score_before})."
            )
        elif validation.status == STATUS_COULD_NOT_IMPROVE:
            parts.append("Automatic fixes could not raise the score — needs human attention.")
    parts.append(f"Review: {review_status}.")
    return " ".join(parts)


def _order_by_gap_priority(
    recs: list[Recommendation], gap: GapResult | None
) -> list[Recommendation]:
    """Most impactful first: order by the criterion's rank in the gap's
    priority-sorted deficiency list; recs off the list keep their original order."""
    if not gap:
        return list(recs)
    rank = {g.criterion: i for i, g in enumerate(gap.criterion_gaps)}
    fallback = len(rank)
    return sorted(recs, key=lambda r: rank.get(r.criterion or "", fallback))
