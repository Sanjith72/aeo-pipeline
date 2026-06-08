"""
Reference Architecture Generator — L3 synthesis (the headline v4 block).

Combines the empirical floor (L1 competitor structural patterns) and the curated
guardrail+ceiling (L2 framework) into a versioned :class:`Blueprint` — an ideal
sitemap + coverage map for one topic. Two-track by design:

  * **Deterministic floor (always).** The framework nodes *are* the base blueprint;
    competitor patterns refine each node's priority (page-types competitors lean
    on get bumped) and annotate the coverage notes. This path needs no LLM and is
    fully reproducible — turn the model off and you still get a complete blueprint.

  * **LLM augmentation (optional).** When an LLM is enabled (set provider=cloud →
    Gemini's OpenAI-compatible endpoint), synthesis *enriches* the floor: extra
    seed questions and net-new supporting pages that fill gaps competitors cover.
    Every LLM proposal is re-validated against the blueprint contract's closed
    vocabularies — an out-of-vocab page-type or a duplicate slug is dropped, never
    trusted. Any failure falls back to the deterministic blueprint.

Mirrors the codebase's deterministic-first principle: the model only *upgrades*
quality; it is never a dependency. Versioning (reuse-vs-bump on the input hash) is
the repo's job — this returns a hash-stamped blueprint; the repo pins it.
"""

from __future__ import annotations

from typing import get_args

from ..logging import get_logger
from ..nlp.llm import LLMClient
from .blueprint import (
    Blueprint,
    CoverageCluster,
    CoverageMap,
    Intent,
    JourneyStage,
    PageType,
    SitemapNode,
    normalize_slug,
)
from .competitor_patterns import CompetitorPatterns
from .config_pin import config_fingerprint
from .framework import Framework, load_framework

log = get_logger(__name__)

# Bound the LLM's net-new proposals so synthesis can't balloon the sitemap.
_MAX_AUGMENT_NODES = 12
_PRIORITY_FLOOR_BUMP = 0.3  # how much a fully-covered page-type lifts node priority
# Bound LLM seed-question enrichment the same way (untrusted/prompt-injectable input).
_MAX_QUESTIONS_PER_SLUG = 5
_MAX_QUESTION_LEN = 300

_SYNTH_SYSTEM = (
    "You are a senior content strategist designing the ideal site for the given "
    "topic and industry. You enrich an existing blueprint; you never invent "
    "page types, intents, or entities outside the allowed lists. Reply with JSON only."
)

# Per-answer-engine emphasis injected into the synthesis prompt (ported idea).
# Routes the PROMPT only — the deterministic floor and closed-vocab guardrail are
# unchanged, so an unknown/garbage target safely falls back to 'generic'.
_ENGINE_EMPHASIS: dict[str, str] = {
    "perplexity": (
        "Engine emphasis (Perplexity): weight CITATION DENSITY — favor pages and seed "
        "questions whose answers are specific, sourceable claims a citation engine can quote."
    ),
    "chatgpt_search": (
        "Engine emphasis (ChatGPT Search): weight CONVERSATIONAL COVERAGE — favor pages that "
        "answer real questions directly in natural language a chat summary would reuse."
    ),
    "gemini": (
        "Engine emphasis (Gemini / Google AI): weight STRUCTURED ENTITY coverage — favor pages "
        "that make the required entities and their relationships explicit and well-structured."
    ),
    "generic": (
        "Engine emphasis (generic): favor substantive, well-structured pages that answer the "
        "topic's real questions."
    ),
}


def _engine_emphasis(engine_target: str | None) -> str:
    return _ENGINE_EMPHASIS.get((engine_target or "generic").lower(), _ENGINE_EMPHASIS["generic"])


def _refine_priority(node: SitemapNode, patterns: CompetitorPatterns | None) -> float:
    """Lift a node's base priority by how heavily competitors cover its page-type
    (the empirical floor). No patterns → base priority unchanged."""
    if patterns is None:
        return node.priority
    share = patterns.page_type_share(node.page_type)
    return min(1.0, round(node.priority + _PRIORITY_FLOOR_BUMP * share, 3))


def _coverage_map(framework: Framework) -> CoverageMap:
    clusters = [
        CoverageCluster(
            name=c.name,
            pillar_slug=c.pillar_slug,
            supporting_slugs=c.supporting_slugs,
            min_pages=c.min_pages,
        )
        for c in framework.clusters
    ]
    return CoverageMap(
        required_entities=list(framework.required_entities),
        journey_stages=list(framework.journey_stages),
        clusters=clusters,
    )


def _deterministic_blueprint(
    topic: str, framework: Framework, patterns: CompetitorPatterns | None
) -> Blueprint:
    nodes = [
        node.model_copy(update={"priority": _refine_priority(node, patterns)})
        for node in framework.nodes
    ]
    notes = "Deterministic synthesis from framework"
    if patterns and patterns.page_count:
        notes += (
            f" + {patterns.page_count} competitor page(s) across "
            f"{len(patterns.domains)} domain(s)"
        )
    return Blueprint(
        topic=topic,
        generator="deterministic",
        framework_version=framework.version,
        competitors=list(patterns.domains) if patterns else [],
        sitemap=nodes,
        coverage=_coverage_map(framework),
        notes=notes,
    )


def _augment_with_llm(
    base: Blueprint,
    framework: Framework,
    patterns: CompetitorPatterns | None,
    llm: LLMClient,
    engine_target: str = "generic",
) -> Blueprint:
    """Ask the LLM for extra seed questions + net-new supporting pages, then
    re-validate every proposal against the contract. Returns ``base`` unchanged on
    any failure."""
    allowed_entities = list(framework.required_entities)
    cluster_names = {c.name for c in framework.clusters}
    existing = {n.slug for n in base.sitemap}

    prompt = _synthesis_prompt(base, framework, patterns, engine_target)
    try:
        data = llm.generate_json(prompt, _SYNTH_SYSTEM)
    except Exception as exc:  # never let synthesis break generation
        log.warning("blueprint_synthesis_failed", error=str(exc))
        return base
    if not isinstance(data, dict):
        return base

    nodes = list(base.sitemap)
    added = 0
    merged_any = False  # did the model contribute any usable seed question?

    # 1) extra seed questions for existing nodes — re-validated + bounded.
    # Cap the slugs processed (can't enrich more nodes than exist), questions per
    # slug, and per-question length; then rebuild the node via model_validate so the
    # contract's dedupe/blank-strip validator runs (model_copy(update=) skips it).
    extra_q = data.get("extra_seed_questions")
    if isinstance(extra_q, dict):
        by_slug = {n.slug: i for i, n in enumerate(nodes)}
        for raw_slug, questions in list(extra_q.items())[: len(nodes)]:
            idx = by_slug.get(normalize_slug(str(raw_slug)))
            if idx is None or not isinstance(questions, list):
                continue
            extra = [str(q)[:_MAX_QUESTION_LEN] for q in questions[:_MAX_QUESTIONS_PER_SLUG]]
            if not extra:
                continue
            before = nodes[idx].seed_questions
            node = SitemapNode.model_validate({**nodes[idx].model_dump(), "seed_questions": [*before, *extra]})
            if len(node.seed_questions) > len(before):  # net-new after dedupe/strip
                merged_any = True
            nodes[idx] = node

    # 2) net-new supporting nodes (bounded + guardrailed)
    for raw in (data.get("augment_nodes") or [])[:_MAX_AUGMENT_NODES]:
        if not isinstance(raw, dict) or "slug" not in raw:
            continue
        slug = normalize_slug(str(raw["slug"]))
        if slug in existing:
            continue
        cluster = raw.get("cluster")
        cluster = cluster if cluster in cluster_names else None
        entities = [e for e in (raw.get("required_entities") or []) if e in allowed_entities]
        try:
            node = SitemapNode.model_validate(
                {
                    "slug": slug,
                    "title": str(raw.get("title", slug)),
                    "page_type": str(raw.get("page_type", "default")),
                    "intent": str(raw.get("intent", "informational")),
                    "journey_stage": str(raw.get("journey_stage", "awareness")),
                    "required_entities": entities,
                    "seed_questions": [str(q) for q in (raw.get("seed_questions") or [])],
                    "cluster": cluster,
                    "priority": 0.55,  # net-new pages start mid; competitors didn't anchor them
                    "rationale": "LLM-proposed to fill a coverage gap",
                }
            )
        except Exception:  # invalid vocabulary → drop it, keep the floor
            continue
        nodes.append(node)
        existing.add(slug)
        added += 1

    if added == 0 and not merged_any:
        return base  # the model added nothing usable → stay deterministic (keep provenance)

    log.info("blueprint_synthesis_applied", model=llm.model, added_nodes=added)
    return base.model_copy(
        update={
            "sitemap": nodes,
            "generator": llm.model,
            "notes": f"{base.notes}; LLM-augmented ({added} net-new node(s)) via {llm.model}",
        }
    )


def _synthesis_prompt(
    base: Blueprint,
    framework: Framework,
    patterns: CompetitorPatterns | None,
    engine_target: str = "generic",
) -> str:
    existing = "\n".join(f"  - {n.slug} [{n.page_type}/{n.intent}] {n.title}" for n in base.sitemap)
    comp = patterns.to_summary() if patterns else {"page_count": 0}
    # Source the allowed vocabularies from the contract's own Literals (not hardcoded
    # strings or config) so the prompt can never advertise a value SitemapNode
    # validation will reject and then silently drop.
    return (
        f"{_engine_emphasis(engine_target)}\n"
        f"Topic: {base.topic}\n"
        f"Allowed page_types: {', '.join(get_args(PageType))}\n"
        f"Allowed intents: {', '.join(get_args(Intent))}\n"
        f"Allowed journey_stages: {', '.join(get_args(JourneyStage))}\n"
        f"Allowed required_entities: {', '.join(framework.required_entities)}\n"
        f"Existing clusters: {', '.join(c.name for c in framework.clusters)}\n\n"
        f"Existing ideal pages:\n{existing}\n\n"
        f"Competitor structural patterns (the empirical floor):\n{comp}\n\n"
        "Propose JSON with two keys:\n"
        '  "extra_seed_questions": an object mapping an existing slug to 1-3 extra '
        "real user questions that page should answer;\n"
        '  "augment_nodes": an array (max 12) of NET-NEW supporting pages that fill '
        "gaps competitors cover but the ideal site above is missing. Each node: "
        '{slug, title, page_type, intent, journey_stage, required_entities, cluster, seed_questions}. '
        "Use only the allowed vocabularies and existing cluster names. Reply JSON only."
    )


def generate_blueprint(
    *,
    topic: str | None = None,
    framework: Framework | None = None,
    patterns: CompetitorPatterns | None = None,
    llm: LLMClient | None = None,
    version: int = 1,
    engine_target: str = "generic",
) -> Blueprint:
    """Synthesize a versioned blueprint from L1 (patterns) + L2 (framework) [+ L3 (llm)].

    Deterministic and complete with no LLM; the LLM only enriches. ``engine_target``
    routes the synthesis prompt's emphasis (Perplexity/ChatGPT/Gemini/generic) and is
    a no-op on the deterministic floor. Returns a hash-stamped blueprint at the given
    provisional ``version`` — the repo decides reuse-vs-bump from the content hash."""
    framework = framework or load_framework()
    topic = topic or framework.topic or "default"

    blueprint = _deterministic_blueprint(topic, framework, patterns)
    if llm is not None and llm.enabled:
        blueprint = _augment_with_llm(blueprint, framework, patterns, llm, engine_target)

    # Pin the scoring-contract config (rubric/targets/thresholds) into the version
    # hash so editing the measuring stick bumps the blueprint version too.
    return blueprint.model_copy(
        update={"version": version, "config_fingerprint": config_fingerprint()}
    ).with_hash()
