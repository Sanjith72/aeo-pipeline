"""
Validation loop — does the proposed fix actually move the score?

For a page's :class:`GapResult` the Recommender proposes edits; this applies them
to a synthetic copy of the extraction bundle, re-scores through the *same*
10-criterion rubric, and compares to the page's deterministic baseline:

  * **improved** (synthetic total > baseline) -> the recommendations are marked
    ``validated`` and the page is sent to Human Review to apply them.
  * **not improved** -> retry the Recommender, capped at ``max_attempts`` (config,
    default 3). After the cap the recommendations are flagged ``needs_review``
    and the page is flagged "could-not-improve" for Human Review.

The re-score uses a deliberately disabled LLM client on both sides, so the gate
measures the *edits'* effect, not LLM scoring noise. Retrying only helps when the
Recommender can vary its output, i.e. an LLM is enabled (a transient model
failure on one attempt may succeed on the next, or yield a more concrete edit);
with no LLM the deterministic recommender repeats itself, so the loop stops after
the first attempt rather than burning identical retries. "Feeding back the failed
attempt" is realized as this re-invocation -- the deterministic, signal-based gate
gains nothing from injecting failure text into the prompt, so we keep the
Recommender stateless.

"Human Review" is a status, not a UI: improved recs carry ``status='validated'``,
could-not-improve recs ``status='needs_review'`` (both are valid recommendations
states), and the page-level outcome carries ``review_status`` for the report.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..logging import get_logger
from ..nlp.llm import LLMClient
from ..processor import GapResult
from ..recommender import Recommendation, recommend
from ..recommender import persist as persist_recs
from ..reference import Reference, load_reference
from ..scoring import score_page
from ..scoring.rubric import Rubric, load_rubric
from ..settings import LLMCfg, get_settings
from ..storage.models import ExtractionBundle
from .simulate import apply_recommendation

log = get_logger(__name__)

# Both sides of the comparison are scored with a deliberately disabled client so
# the gate measures the *edits'* effect, never run-to-run LLM scoring noise. (The
# aggregator substitutes the real client for ``llm=None``, so we pass this instead.)
_DETERMINISTIC_LLM = LLMClient(LLMCfg(enabled=False))

# Page-level validation outcome (ValidationOutcome.status).
STATUS_IMPROVED = "improved"
STATUS_COULD_NOT_IMPROVE = "could_not_improve"
STATUS_NO_ACTION = "no_action"

# Per-recommendation DB status (must satisfy the recommendations.status CHECK).
_REC_VALIDATED = "validated"
_REC_NEEDS_REVIEW = "needs_review"

# Page-level review routing (feeds page_reports.review_status in the report block).
REVIEW_NEEDED = "needs_review"
REVIEW_NONE = "none"


@dataclass(slots=True)
class ValidationOutcome:
    page_id: int
    run_id: int
    status: str                 # improved | could_not_improve | no_action
    improved: bool
    attempts: int
    score_before: int           # deterministic rubric total before edits
    score_after: int            # deterministic rubric total on the synthetic page
    review_status: str          # needs_review | none
    recommendations: list[Recommendation] = field(default_factory=list)
    rec_ids: list[int] = field(default_factory=list)  # persisted ids ([] if not persisted)


def validate_page(
    bundle: ExtractionBundle,
    gap: GapResult,
    *,
    url: str,
    reference: Reference | None = None,
    rubric: Rubric | None = None,
    llm: LLMClient | None = None,
    page_type: str | None = None,
    max_attempts: int | None = None,
    persist: bool = True,
    recommend_fn=recommend,
    score_fn=score_page,
) -> ValidationOutcome:
    """Run the recommend -> simulate -> re-score -> retry loop for one page."""
    reference = reference or load_reference()
    rubric = rubric or load_rubric()
    if max_attempts is None:
        max_attempts = get_settings().validation.max_attempts
    max_attempts = max(1, int(max_attempts))

    page_id, run_id = gap.page_id, gap.run_id
    baseline_score = score_fn(bundle, run_id, llm=_DETERMINISTIC_LLM, rubric=rubric)
    det_before = baseline_score.total

    # No deficiencies -> nothing to validate or review.
    if not gap.criterion_gaps:
        return ValidationOutcome(
            page_id=page_id,
            run_id=run_id,
            status=STATUS_NO_ACTION,
            improved=False,
            attempts=0,
            score_before=det_before,
            score_after=det_before,
            review_status=REVIEW_NONE,
        )

    best_recs: list[Recommendation] = []
    det_after = det_before
    improved = False
    attempts = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        best_recs = recommend_fn(
            bundle, gap, url=url, reference=reference, llm=llm, page_type=page_type
        )
        synthetic = copy.deepcopy(bundle)
        for rec in best_recs:
            apply_recommendation(synthetic, rec, rubric=rubric, reference=reference)
        det_after = score_fn(synthetic, run_id, llm=_DETERMINISTIC_LLM, rubric=rubric).total

        if det_after > det_before:
            improved = True
            break
        # A deterministic recommender repeats itself -> retrying is futile.
        if llm is None or not llm.enabled:
            break

    status = STATUS_IMPROVED if improved else STATUS_COULD_NOT_IMPROVE
    rec_status = _REC_VALIDATED if improved else _REC_NEEDS_REVIEW

    log.info(
        "validation_complete",
        page_id=page_id,
        run_id=run_id,
        status=status,
        attempts=attempts,
        score_before=det_before,
        score_after=det_after,
        recommendations=len(best_recs),
    )

    rec_ids: list[int] = []
    if persist and best_recs:
        # Pin each targeted criterion's baseline tier so a re-crawl can verify the fix
        # actually landed (not just that the page changed).
        baseline_tiers = {name: c.value for name, c in baseline_score.criteria.items()}
        rec_ids = _persist(
            page_id, run_id, best_recs, attempts, det_before, det_after,
            rec_status, improved, baseline_tiers,
        )

    return ValidationOutcome(
        page_id=page_id,
        run_id=run_id,
        status=status,
        improved=improved,
        attempts=attempts,
        score_before=det_before,
        score_after=det_after,
        review_status=REVIEW_NEEDED,
        recommendations=best_recs,
        rec_ids=rec_ids,
    )


def _persist(
    page_id: int,
    run_id: int,
    recs: list[Recommendation],
    attempt: int,
    score_before: int,
    score_after: int,
    rec_status: str,
    improved: bool,
    baseline_tiers: dict[str, int],
) -> list[int]:
    """Write the proposed recs, then stamp each with its validation outcome."""
    from ..storage.repos import recommendations as recs_repo

    rec_ids = persist_recs(page_id, run_id, recs, attempt=attempt, score_before=score_before)
    for rid in rec_ids:
        recs_repo.set_validation(
            rid, status=rec_status, validated=improved, score_after=score_after
        )
    _open_outcomes(page_id, run_id, rec_ids, recs, baseline_tiers)
    return rec_ids


def _open_outcomes(
    page_id: int, run_id: int, rec_ids: list[int], recs: list[Recommendation],
    baseline_tiers: dict[str, int],
) -> None:
    """Retention Engine (#11): open a pending outcome per issued recommendation,
    pinning the page's current content hash as the baseline the next re-crawl
    compares against. This is THE rec-create call site, so detection bookkeeping is
    opened here exactly once per persisted rec.

    Best-effort and flag-gated; a no-op for synthetic page ids (dry-run uses 0), so
    the offline tests that monkeypatch the recommendations repo never reach a real
    connection. A failure here is logged, never raised — issuing the rec must not
    depend on the outcome log."""
    if page_id <= 0 or not get_settings().retention.enabled:
        return
    try:
        from ..storage.repos import outcomes as outcomes_repo
        from ..storage.repos import pages as pages_repo

        page = pages_repo.get(page_id)
        if not page:
            return
        url_normalized = page.get("url_normalized")
        if not url_normalized:
            return
        baseline_hash = page.get("content_hash")
        for rid, rec in zip(rec_ids, recs, strict=True):  # 1 persisted id per rec
            outcomes_repo.open(
                rid, page_id, url_normalized,
                baseline_run_id=run_id, baseline_hash=baseline_hash,
                criterion=rec.criterion,
                baseline_tier=baseline_tiers.get(rec.criterion) if rec.criterion else None,
            )
    except Exception as exc:  # outcome bookkeeping must never break rec issuance
        log.warning("outcome_open_skipped", page_id=page_id, error=str(exc))
