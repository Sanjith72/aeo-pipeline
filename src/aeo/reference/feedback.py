"""
Validated-wins feedback loop — controlled, human-gated learning.

The controlled version of "the system evolves itself." Pages that *provably get
cited* (the Independent Validator's Perplexity signal) are compared against pages
that don't, per rubric criterion. Where the cited cohort consistently and
materially out-tiers both the current target and the non-cited cohort, this
proposes nudging that criterion's **target** upward.

Two guard rails make this learning rather than drift:

  * **Direction.** Cited wins refine the *criteria definitions*, never the
    blueprint or the client's own recommendations — that would be circular
    validation one level up. We touch only the rubric target.
  * **Human-gated.** Every output is a ``CriteriaRefinement`` with
    ``status='proposed'``. The system never auto-applies; a human accepts/rejects,
    and only then does ``config/best_practices.yaml`` change. A thin sample or a
    weak signal proposes nothing.

Pure and deterministic over citation observations; persistence is a thin repo.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from ..scoring.rubric import Rubric, load_rubric
from .loader import Reference, load_reference

# Don't propose anything off a thin sample.
MIN_CITED_SAMPLE = 3
# The cited cohort must beat the non-cited cohort by at least this many tiers.
DEFAULT_MARGIN = 1


@dataclass(slots=True)
class CitationObservation:
    """One page's citation outcome plus its per-criterion tiers (1-5)."""

    page_id: int
    url: str
    cited: bool
    tiers: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class CriteriaRefinement:
    criterion: str
    # Nullable: rubric proposals carry numeric tier targets; an override-derived proposal
    # (R2-4) has no numeric target and leaves both None (the values live in ``evidence``).
    current_target: int | None
    proposed_target: int | None
    rationale: str
    evidence: dict
    status: str = "proposed"

    def to_row(self) -> dict:
        return asdict(self)


# Override-derived proposals are namespaced on the criterion so they're distinguishable
# from rubric-tier proposals in the same human-gated queue.
_OVERRIDE_PREFIX = "override:"


def propose_refinement_from_override(
    *, field: str, old_value: object, new_value: object, kind: str = "field_override"
) -> CriteriaRefinement:
    """Turn a human override — an edited prefill, or a rejected recommendation — into a
    **proposed** refinement for the human-gated queue.

    The learning loop is capture → propose → human-validate. This function ALWAYS returns
    ``status='proposed'`` and never touches an existing refinement: per the v4 design the
    system must never auto-apply (or auto-tune toward) a learned change, to avoid the
    circular-validation trap. A human reviews every proposal via ``set_refinement_status``."""
    return CriteriaRefinement(
        criterion=f"{_OVERRIDE_PREFIX}{field}"[:40],
        current_target=None,
        proposed_target=None,
        rationale=(
            f"User {kind.replace('_', ' ')} on '{field}': {old_value!r} → {new_value!r}. "
            "Captured as a proposal for human review; not auto-applied."
        ),
        evidence={"kind": kind, "field": field, "old": old_value, "new": new_value},
        status="proposed",
    )


def _median(values: list[int]) -> float | None:
    return statistics.median(values) if values else None


def propose_criteria_refinements(
    observations: list[CitationObservation],
    *,
    reference: Reference | None = None,
    rubric: Rubric | None = None,
    min_cited: int = MIN_CITED_SAMPLE,
    margin: int = DEFAULT_MARGIN,
) -> list[CriteriaRefinement]:
    """Propose target nudges for criteria where cited pages clearly outperform.

    Returns an empty list when the cited sample is too thin or no criterion shows
    a strong enough signal — proposing nothing is the correct, common outcome."""
    reference = reference or load_reference()
    rubric = rubric or load_rubric()

    cited = [o for o in observations if o.cited]
    uncited = [o for o in observations if not o.cited]
    if len(cited) < min_cited:
        return []

    proposals: list[CriteriaRefinement] = []
    for criterion in rubric.names():
        cited_tiers = [o.tiers[criterion] for o in cited if criterion in o.tiers]
        if len(cited_tiers) < min_cited:
            continue
        uncited_tiers = [o.tiers[criterion] for o in uncited if criterion in o.tiers]

        cited_med = _median(cited_tiers)
        um = _median(uncited_tiers)
        uncited_med = um if um is not None else float(rubric.scale_min)
        current = reference.target_for(criterion)

        # Signal: cited cohort consistently exceeds the current target AND beats
        # the non-cited cohort by the margin.
        if cited_med is None or cited_med < current + 1:
            continue
        if (cited_med - uncited_med) < margin:
            continue

        proposed = min(rubric.scale_max, max(current + 1, round(cited_med)))
        if proposed <= current:
            continue

        proposals.append(
            CriteriaRefinement(
                criterion=criterion,
                current_target=current,
                proposed_target=proposed,
                rationale=(
                    f"Cited pages median {cited_med:g}/5 on '{criterion}' vs "
                    f"{uncited_med:g}/5 for non-cited; current target {current}. "
                    f"Cited content consistently exceeds the target — propose raising it."
                ),
                evidence={
                    "cited_n": len(cited_tiers),
                    "uncited_n": len(uncited_tiers),
                    "cited_median": cited_med,
                    "uncited_median": uncited_med,
                    "current_target": current,
                },
            )
        )

    return proposals
