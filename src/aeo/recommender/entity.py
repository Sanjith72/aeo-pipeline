"""
Entity recommender — fix brand-vs-first-person consistency (criterion 4).

Entity Consistency scores the ratio of canonical brand mentions to first-person
language ("we/our/us"). Cybersecurity marketing pages routinely run first-person
2:1 over the brand name, which reads as anonymous to an answer engine trying to
attribute a claim to a named organization.

This proposes concrete rewrites that swap first-person phrasing for the page's
canonical entity name. Deterministic-first: when the LLM is disabled or fails, a
criterion-specific advisory still names the exact brand to use (resolved from the
bundle's primary entity) so the suggestion is grounded rather than generic.
"""

from __future__ import annotations

from ..nlp.llm import LLMClient
from ..reference import Reference, load_reference
from ..storage.models import ExtractionBundle
from .models import ENTITY, Recommendation

# The single criterion this generator owns.
ENTITY_CRITERIA = {"entity_consistency"}


def recommend_entity(
    bundle: ExtractionBundle,
    targets: list[str],
    *,
    reference: Reference | None = None,
    llm: LLMClient | None = None,
) -> list[Recommendation]:
    reference = reference or load_reference()
    recs: list[Recommendation] = []
    for criterion in targets:
        if criterion not in ENTITY_CRITERIA:
            continue
        rec: Recommendation | None = None
        if llm is not None and llm.enabled:
            rec = _llm_edit(bundle, reference, llm)
        recs.append(rec or _advisory(bundle, reference))
    return recs


def _state(bundle: ExtractionBundle) -> tuple[str | None, int, int, float | None]:
    """(primary entity name, entity mentions, first-person mentions, ratio)."""
    ent = bundle.get("entities", {}) or {}
    primary = ent.get("primary") or {}
    name = primary.get("name")
    entity_count = int(ent.get("entity_count", 0) or 0)
    first_person = int(ent.get("first_person_count", 0) or 0)
    ratio = ent.get("ratio")
    return name, entity_count, first_person, ratio


def _grounding(reference: Reference, name: str | None, entity_count: int, first_person: int) -> str:
    target = reference.target_for("entity_consistency")
    brand = name or "the organization"
    return (
        f"Target score for 'entity_consistency': {target}/5. "
        f"The page names {brand} {entity_count} time(s) but uses first-person "
        f"language (we/our/us) {first_person} time(s); answer engines attribute "
        "claims to a named entity, not an anonymous 'we'."
    )


def _excerpt(bundle: ExtractionBundle) -> str:
    meta = bundle.get("meta", {}) or {}
    headings = bundle.get("headings", {}) or {}
    h2 = ((headings.get("by_level", {}) or {}).get("h2", []) or [])[:6]
    return "\n".join(
        [
            f"Title: {meta.get('title', '')}",
            f"H1: {headings.get('h1_text', '')}",
            f"H2 headings: {' | '.join(h2)}",
        ]
    )


def _llm_edit(bundle: ExtractionBundle, reference: Reference, llm: LLMClient) -> Recommendation | None:
    name, entity_count, first_person, _ratio = _state(bundle)
    brand = name or "the organization"
    system = (
        "You are an AEO (Answer Engine Optimization) editor for cybersecurity content. "
        "Rewrite first-person marketing copy so a named organization owns each claim. "
        'Reply as JSON with keys "summary" (one sentence) and "edits" (an array of '
        "concrete before/after rewrite strings)."
    )
    prompt = (
        f"Entity to foreground: {brand}\n"
        f"Best practice: {_grounding(reference, name, entity_count, first_person)}\n\n"
        f"Current page:\n{_excerpt(bundle)}\n\n"
        "Propose specific rewrites that replace 'we/our/us' with the entity name "
        "where it strengthens attribution. Return JSON only."
    )
    data = llm.generate_json(prompt, system)
    if not data:
        return None
    edits = data.get("edits")
    if isinstance(edits, str):
        edits = [edits]
    if not edits:
        return None
    summary = str(data.get("summary") or "Improve entity consistency")
    return Recommendation(
        rec_type=ENTITY,
        criterion="entity_consistency",
        title=summary,
        rationale=_grounding(reference, name, entity_count, first_person),
        payload={"edits": [str(e) for e in edits], "primary_entity": name},
        scored_by=llm.model,
    )


def _advisory(bundle: ExtractionBundle, reference: Reference) -> Recommendation:
    name, entity_count, first_person, _ratio = _state(bundle)
    brand = name or "your organization"
    if entity_count == 0:
        guidance = (
            f"The page never names {brand}. State the organization's name explicitly "
            "in the intro and when making claims, so an answer engine can attribute "
            "the content to a known entity."
        )
    else:
        guidance = (
            f"Replace first-person phrasing (we/our/us) with '{brand}' in claims and "
            f"descriptions. The page names {brand} {entity_count} time(s) against "
            f"{first_person} first-person reference(s); aim for at least parity so the "
            "brand — not an anonymous 'we' — owns each claim."
        )
    return Recommendation(
        rec_type=ENTITY,
        criterion="entity_consistency",
        title="Improve entity consistency",
        rationale=_grounding(reference, name, entity_count, first_person),
        payload={"guidance": guidance, "primary_entity": name},
        scored_by="deterministic",
    )
