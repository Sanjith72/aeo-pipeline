"""
Content recommender — LLM-authored edits, deterministic-first.

For content-shaped deficiencies (thin Q&A, missing stats, weak headings, low
readability, …) this asks the LLM for concrete edits, grounding the prompt in
Reference-Layer best practices (per-criterion target + the page-type's ideal
architecture) so suggestions are specific rather than generic.

Deterministic-first: when the LLM is disabled or fails, every targeted criterion
still yields a static, criterion-specific advisory enriched with the reference
architecture. The system stays useful with no model available — the LLM only
*upgrades* the suggestion quality.
"""

from __future__ import annotations

from ..nlp.llm import LLMClient
from ..reference import Reference, load_reference
from ..storage.models import ExtractionBundle
from .models import CONTENT, Recommendation

# Everything the schema/entity generators don't own.
CONTENT_CRITERIA = {
    "qa_blocks",
    "stats_in_html",
    "content_depth",
    "heading_structure",
    "answer_readability",
    "citation_signals",
    "load_speed",
    "render_accessibility",
}

# Of those, the ones worth an LLM rewrite (the rest are technical/advisory only).
_LLM_CRITERIA = {
    "qa_blocks",
    "stats_in_html",
    "content_depth",
    "heading_structure",
    "answer_readability",
}

_ADVISORY: dict[str, str] = {
    "qa_blocks": (
        "Add an FAQ section: 3-6 questions phrased the way your audience asks them, "
        "each answered in 2-4 concise sentences placed directly under the question heading."
    ),
    "stats_in_html": (
        "Add concrete, sourced statistics as plain HTML text — percentages, counts, "
        "amounts, dates. Answer engines preferentially quote specific numbers."
    ),
    "content_depth": (
        "Deepen the page with methodology, data, and worked examples; thin content "
        "rarely earns citations."
    ),
    "heading_structure": (
        "Rewrite H2/H3 headings as the questions users ask or as named concepts "
        "(e.g. 'What is X?', 'How does Y work?') under a single descriptive H1."
    ),
    "answer_readability": (
        "Tighten the prose: shorter sentences, one idea per paragraph, and clear "
        "sub-sections so an engine can lift a clean answer passage."
    ),
    "citation_signals": (
        "Add an author byline with credentials, a visible publish/updated date, and "
        "links to authoritative primary sources and recognized standards bodies for the topic."
    ),
    "load_speed": (
        "Improve mobile performance: compress images, defer non-critical JS, and cache "
        "static assets — slow pages are weighted lower."
    ),
    "render_accessibility": (
        "Serve key content in the server-rendered HTML rather than injecting it with "
        "client-side JS, which answer engines may never execute."
    ),
}


def recommend_content(
    bundle: ExtractionBundle,
    targets: list[str],
    *,
    reference: Reference | None = None,
    llm: LLMClient | None = None,
    page_type: str | None = None,
) -> list[Recommendation]:
    reference = reference or load_reference()
    recs: list[Recommendation] = []
    for criterion in targets:
        if criterion not in CONTENT_CRITERIA:
            continue
        rec: Recommendation | None = None
        if criterion in _LLM_CRITERIA and llm is not None and llm.enabled:
            rec = _llm_edit(bundle, criterion, reference, llm, page_type)
        recs.append(rec or _advisory(criterion, reference, page_type))
    return recs


def _grounding(reference: Reference, criterion: str, page_type: str | None) -> str:
    target = reference.target_for(criterion)
    lines = [f"Target score for '{criterion}': {target}/5."]
    if page_type:
        arch = reference.architecture_for(page_type)
        if arch.must_have:
            lines.append(f"A {page_type} page should include: {', '.join(arch.must_have)}.")
        if arch.target_word_count:
            lines.append(f"Aim for ~{arch.target_word_count} words.")
    return " ".join(lines)


def _excerpt(bundle: ExtractionBundle) -> str:
    meta = bundle.get("meta", {}) or {}
    headings = bundle.get("headings", {}) or {}
    by_level = headings.get("by_level", {}) or {}
    h2 = (by_level.get("h2", []) or [])[:8]
    read = bundle.get("readability", {}) or {}
    parts = [
        f"Title: {meta.get('title', '')}",
        f"H1: {headings.get('h1_text', '')}",
        f"H2 headings: {' | '.join(h2)}",
        f"Word count: {read.get('word_count', 'unknown')}",
    ]
    return "\n".join(parts)


# Bound the ready-to-publish draft a model may return (untrusted, cost/abuse guard).
_MAX_DRAFT_LEN = 8000


def _llm_edit(
    bundle: ExtractionBundle,
    criterion: str,
    reference: Reference,
    llm: LLMClient,
    page_type: str | None,
) -> Recommendation | None:
    # Topic/industry-agnostic: the model infers the subject from the page excerpt, so
    # this works for any vertical (healthcare, finance, legal, …), not just security.
    system = (
        "You are an AEO (Answer Engine Optimization) editor. Improve the specified "
        "criterion by writing the ACTUAL ready-to-publish replacement copy for this "
        "page's topic — real headings and prose an editor can paste in, never generic "
        "advice about what to write. Reply as JSON with keys: \"summary\" (one sentence), "
        '"edits" (an array of concrete change descriptions), and "draft" (the finished, '
        "ready-to-publish HTML or Markdown for this criterion's section)."
    )
    prompt = (
        f"Criterion to improve: {criterion}\n"
        f"Best practice: {_grounding(reference, criterion, page_type)}\n\n"
        f"Current page:\n{_excerpt(bundle)}\n\n"
        "Write the finished copy in \"draft\" (do not describe it — produce it). Return JSON only."
    )
    data = llm.generate_json(prompt, system)
    if not data:
        return None
    edits = data.get("edits")
    if isinstance(edits, str):
        edits = [edits]
    draft = data.get("draft")
    draft = str(draft).strip()[:_MAX_DRAFT_LEN] if draft else ""
    # Usable only if the model returned either concrete edits or finished copy.
    if not edits and not draft:
        return None
    summary = str(data.get("summary") or f"Improve {criterion}")
    payload: dict = {"edits": [str(e) for e in (edits or [])]}
    if draft:
        payload["draft"] = draft  # the ready-to-publish body copy / headers
    return Recommendation(
        rec_type=CONTENT,
        criterion=criterion,
        title=summary,
        rationale=_grounding(reference, criterion, page_type),
        payload=payload,
        scored_by=llm.model,
    )


def _advisory(criterion: str, reference: Reference, page_type: str | None) -> Recommendation:
    guidance = _ADVISORY.get(criterion, f"Improve {criterion} toward the rubric target.")
    # Enrich the depth advisory with the page-type word-count goal when known.
    if criterion == "content_depth" and page_type:
        wc = reference.architecture_for(page_type).target_word_count
        if wc:
            guidance = f"{guidance} Aim for ~{wc} words for a {page_type} page."
    return Recommendation(
        rec_type=CONTENT,
        criterion=criterion,
        title=f"Improve {criterion}",
        rationale=_grounding(reference, criterion, page_type),
        payload={"guidance": guidance},
        scored_by="deterministic",
    )
