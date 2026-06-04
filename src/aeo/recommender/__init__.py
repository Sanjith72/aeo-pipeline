"""
Recommender block — turn a GapResult into concrete, persistable edits.

Three generators, each owning a slice of the 10-criterion rubric:

  * ``schema.py``  — deterministic JSON-LD for ``schema_markup`` (zero hallucination).
  * ``entity.py``  — brand-vs-first-person rewrites for ``entity_consistency``.
  * ``content.py`` — LLM/advisory edits for the remaining eight content criteria.

``recommend`` routes each *deficient* criterion (gap > 0, per the Dual-Layer Gap
Analysis) to its owning generator and returns a flat list of
:class:`Recommendation`. Deterministic-first throughout: with no LLM the system
still emits grounded, criterion-specific advisories. ``persist`` writes the
results to the recommendations repo — one row per rec, tagged with the Validation
attempt — so the Validation loop can re-score and update each by id.
"""

from __future__ import annotations

from ..nlp.llm import LLMClient
from ..processor import GapResult
from ..reference import Reference, load_reference
from ..storage.models import ExtractionBundle
from .content import recommend_content
from .entity import recommend_entity
from .models import CONTENT, ENTITY, SCHEMA, Recommendation
from .schema import recommend_schema

__all__ = [
    "CONTENT",
    "ENTITY",
    "SCHEMA",
    "Recommendation",
    "persist",
    "recommend",
    "recommend_content",
    "recommend_entity",
    "recommend_schema",
]


def recommend(
    bundle: ExtractionBundle,
    gap: GapResult,
    *,
    url: str,
    reference: Reference | None = None,
    llm: LLMClient | None = None,
    page_type: str | None = None,
) -> list[Recommendation]:
    """Route every deficient criterion in ``gap`` to its generator.

    ``schema_markup`` goes to the deterministic JSON-LD builder; the content and
    entity generators self-filter the criterion list to the slice they own, so
    passing the full deficient list to each is safe.
    """
    reference = reference or load_reference()
    deficient = [g.criterion for g in gap.criterion_gaps]
    recs: list[Recommendation] = []

    if "schema_markup" in deficient:
        recs.extend(recommend_schema(bundle, url=url))

    recs.extend(recommend_entity(bundle, deficient, reference=reference, llm=llm))
    recs.extend(
        recommend_content(bundle, deficient, reference=reference, llm=llm, page_type=page_type)
    )
    return recs


def persist(
    page_id: int,
    run_id: int,
    recs: list[Recommendation],
    *,
    attempt: int = 1,
    score_before: float | None = None,
) -> list[int]:
    """Write recommendations to the repo; returns the inserted row ids."""
    from ..storage.repos import recommendations as recs_repo

    ids: list[int] = []
    for rec in recs:
        rec_id = recs_repo.create(
            page_id,
            run_id,
            rec.rec_type,
            rec.to_payload(),
            criterion=rec.criterion,
            attempt=attempt,
            score_before=score_before,
        )
        ids.append(rec_id)
    return ids
