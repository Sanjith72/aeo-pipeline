"""
Framework bootstrap — make the tool work for ANY website, not just Securin.

The per-page rubric is already topic-agnostic. The only Securin-specific piece is the
ideal-site *framework* (``config/framework.yaml``: clusters, ideal pages, entities) that
drives the blueprint + site-level coverage diff. This module generates a per-domain
framework so any site can be AEO-optimized:

  * :func:`generic_framework` — a deterministic, universal ideal-site skeleton (homepage,
    product, solutions, pricing, a resources/FAQ content cluster, about, contact). Works
    for any business site with zero curation; coverage then flags which of these a site
    is missing.
  * :func:`bootstrap_framework` — starts from the generic skeleton and, when an LLM is
    available, tailors it to the domain's actual topic (real entities + topic clusters +
    seed questions), re-validated against the closed vocabulary. Falls back to the generic
    skeleton when the LLM is off/fails — deterministic-first.
  * :func:`write_framework` — writes ``config/domains/{domain}.framework.yaml``, which
    :func:`aeo.reference.framework.load_framework` picks up automatically for that domain.

Onboarding any site becomes:  ``aeo framework bootstrap <domain>`` → review the file →
``aeo add-target … && aeo audit-cycle <domain> -t …``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import yaml

from ..logging import get_logger
from ..nlp.llm import LLMClient
from ..settings import get_settings
from .blueprint import Intent, JourneyStage, PageType, SitemapNode, normalize_slug
from .domain_config import normalize_domain
from .taxonomic_ceiling import ceiling_prompt_clause, ceiling_standards, merge_ceiling_entities

log = get_logger(__name__)

_PAGE_TYPES = set(get_args(PageType))
_INTENTS = set(get_args(Intent))
_STAGES = set(get_args(JourneyStage))

_MAX_CLUSTERS = 5
_MAX_NODES_PER_CLUSTER = 5
_MAX_STANDALONE = 8
_MAX_ENTITIES = 20


def _topic_from_domain(domain: str) -> str:
    """Best-effort readable topic from a bare domain (``acme-corp.com`` → ``Acme Corp``)."""
    host = normalize_domain(domain)
    label = host.split(".")[0] if host else "Site"
    return " ".join(w.capitalize() for w in label.replace("-", " ").replace("_", " ").split()) or "Site"


def generic_framework(domain: str, topic: str | None = None, category: str | None = None) -> dict[str, Any]:
    """A universal, deterministic ideal-site skeleton — valid for ANY business site.

    When ``category`` matches a curated vertical, its Taxonomic Ceiling standards
    (HIPAA, SEC, …) seed ``required_entities`` so the regulatory ceiling holds even with
    no LLM. Unknown/no category → empty entity set (the prior behavior)."""
    topic = topic or _topic_from_domain(domain)
    return {
        "version": "1",
        "topic": topic,
        "required_entities": ceiling_standards(category),
        "journey_stages": ["awareness", "consideration", "decision"],
        "clusters": [
            {
                "name": "core-content",
                "min_pages": 3,
                "pillar": {
                    "slug": "/resources", "title": "Resources / Knowledge Hub",
                    "page_type": "pillar", "intent": "informational", "journey_stage": "awareness",
                    "seed_questions": [f"What does {topic} do?", f"How does {topic} work?"],
                },
                "supporting": [
                    {"slug": "/faq", "title": "Frequently Asked Questions", "page_type": "pillar",
                     "intent": "informational", "journey_stage": "consideration",
                     "seed_questions": ["What are the most common questions about this product?"]},
                    {"slug": "/blog", "title": "Blog / Articles", "page_type": "blog",
                     "intent": "informational", "journey_stage": "awareness"},
                ],
            },
        ],
        "standalone_nodes": [
            {"slug": "/", "title": f"{topic} — Homepage", "page_type": "homepage",
             "intent": "navigational", "journey_stage": "awareness"},
            {"slug": "/product", "title": "Product / Platform", "page_type": "product",
             "intent": "commercial", "journey_stage": "decision",
             "seed_questions": [f"What does {topic} offer?"]},
            {"slug": "/solutions", "title": "Solutions / Use Cases", "page_type": "solution",
             "intent": "commercial", "journey_stage": "consideration"},
            {"slug": "/pricing", "title": "Pricing", "page_type": "product",
             "intent": "commercial", "journey_stage": "decision"},
            {"slug": "/about", "title": "About", "page_type": "about",
             "intent": "navigational", "journey_stage": "awareness"},
            {"slug": "/contact", "title": "Contact / Request a Demo", "page_type": "contact",
             "intent": "commercial", "journey_stage": "decision"},
        ],
    }


_BOOTSTRAP_SYSTEM = (
    "You are an AEO (Answer Engine Optimization) strategist. You design the IDEAL set of "
    "pages a website should have to be cited by AI answer engines for its topic. Reply with JSON only."
)


def _bootstrap_prompt(domain: str, topic_hint: str, category: str | None = None) -> str:
    return (
        f"Website: {domain}\n"
        f"Working topic guess: {topic_hint}\n"
        f"{ceiling_prompt_clause(category)}\n"
        "Design the ideal site for THIS website's actual topic. Return JSON with keys:\n"
        '  "topic": a short topic name;\n'
        '  "required_entities": 8-15 REAL product/company/standard/concept names central to this '
        "site's topic, INCLUDING the regulatory/compliance standards from the taxonomic ceiling "
        "above when a category was given (no placeholders);\n"
        '  "clusters": 2-4 topical-authority clusters, each {name, min_pages (int 5-10), '
        "pillar:{slug,title,page_type,intent,journey_stage,required_entities,seed_questions}, "
        "supporting:[ up to 3 of the same node shape ]};\n"
        '  "standalone_nodes": homepage/product/contact-style pages as the same node shape.\n'
        f"Allowed page_type: {', '.join(sorted(_PAGE_TYPES))}.\n"
        f"Allowed intent: {', '.join(sorted(_INTENTS))}.\n"
        f"Allowed journey_stage: {', '.join(sorted(_STAGES))}.\n"
        "required_entities on a node must be a subset of the top-level required_entities. "
        "Slugs are URL paths like /what-is-x. Reply JSON only."
    )


def _clean_node(raw: dict, allowed_entities: set[str]) -> dict[str, Any] | None:
    """Validate one node against the closed vocab via SitemapNode; return a clean dict or None."""
    if not isinstance(raw, dict) or not raw.get("slug"):
        return None
    page_type = str(raw.get("page_type", "default"))
    intent = str(raw.get("intent", "informational"))
    stage = str(raw.get("journey_stage", "awareness"))
    if page_type not in _PAGE_TYPES or intent not in _INTENTS or stage not in _STAGES:
        return None
    entities = [e for e in (raw.get("required_entities") or []) if str(e) in allowed_entities]
    try:
        node = SitemapNode.model_validate({
            "slug": normalize_slug(str(raw["slug"])),
            "title": str(raw.get("title", raw["slug"])),
            "page_type": page_type, "intent": intent, "journey_stage": stage,
            "required_entities": entities,
            "seed_questions": [str(q) for q in (raw.get("seed_questions") or [])],
        })
    except Exception:
        return None
    return {
        "slug": node.slug, "title": node.title, "page_type": node.page_type,
        "intent": node.intent, "journey_stage": node.journey_stage,
        "required_entities": node.required_entities, "seed_questions": node.seed_questions,
    }


def bootstrap_framework(
    domain: str,
    *,
    llm: LLMClient | None = None,
    topic: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Generate a per-domain framework. Deterministic generic skeleton, tailored by the LLM
    when available (re-validated against the contract; falls back cleanly on any failure).

    ``category`` (e.g. 'healthcare', 'personal finance', 'legal services') fuses an
    industry-appropriate Taxonomic Ceiling: its regulatory/compliance standards are
    injected into the synthesis prompt AND unioned into ``required_entities`` so the
    ceiling survives regardless of what the LLM returns. Unknown/no category → the prior
    generic behavior."""
    fw = generic_framework(domain, topic=topic, category=category)
    if llm is None or not llm.enabled:
        return fw

    try:
        data = llm.generate_json(_bootstrap_prompt(domain, fw["topic"], category), _BOOTSTRAP_SYSTEM)
    except Exception as exc:
        log.warning("framework_bootstrap_llm_failed", domain=domain, error=str(exc))
        return fw
    if not isinstance(data, dict):
        return fw

    # Union the curated ceiling standards in first so a category's regulatory entities are
    # guaranteed present even if the model omitted them; cap at _MAX_ENTITIES.
    llm_entities = [str(e) for e in (data.get("required_entities") or [])]
    entities = merge_ceiling_entities(category, llm_entities, limit=_MAX_ENTITIES)
    allowed = set(entities)
    clusters: list[dict] = []
    for craw in (data.get("clusters") or [])[:_MAX_CLUSTERS]:
        if not isinstance(craw, dict):
            continue
        pillar = _clean_node(craw.get("pillar") or {}, allowed)
        if pillar is None:
            continue
        supporting = [n for n in (_clean_node(s, allowed) for s in (craw.get("supporting") or [])[:_MAX_NODES_PER_CLUSTER]) if n]
        clusters.append({
            "name": str(craw.get("name", "cluster")),
            "min_pages": max(1, int(craw.get("min_pages", 5)) if str(craw.get("min_pages", 5)).isdigit() else 5),
            "pillar": pillar,
            "supporting": supporting,
        })
    standalone = [n for n in (_clean_node(s, allowed) for s in (data.get("standalone_nodes") or [])[:_MAX_STANDALONE]) if n]

    if not clusters and not standalone:
        log.info("framework_bootstrap_empty_llm_output", domain=domain)
        return fw  # nothing usable — keep the generic skeleton

    # De-dupe slugs across the whole framework (the loader rejects duplicates).
    seen: set[str] = set()
    def _dedupe(nodes: list[dict]) -> list[dict]:
        out = []
        for n in nodes:
            if n["slug"] in seen:
                continue
            seen.add(n["slug"])
            out.append(n)
        return out

    for c in clusters:
        kept = _dedupe([c["pillar"], *c["supporting"]])
        c["pillar"] = kept[0] if kept else c["pillar"]
        c["supporting"] = kept[1:]
    standalone = _dedupe(standalone)

    return {
        "version": "1",
        "topic": str(data.get("topic") or fw["topic"]),
        "required_entities": entities,
        "journey_stages": ["awareness", "consideration", "decision"],
        "clusters": clusters or fw["clusters"],
        "standalone_nodes": standalone or fw["standalone_nodes"],
        "_generated_by": f"bootstrap:{llm.model}",
    }


def framework_file_path(domain: str, config_dir: Path | None = None) -> Path:
    base = config_dir or Path(get_settings().config_dir)
    return base / "domains" / f"{normalize_domain(domain)}.framework.yaml"


def write_framework(domain: str, data: dict[str, Any], *, config_dir: Path | None = None) -> Path:
    """Write a per-domain framework to ``config/domains/{domain}.framework.yaml``."""
    path = framework_file_path(domain, config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Auto-generated AEO framework for {normalize_domain(domain)}.\n"
        f"# Review + edit, then: aeo add-target <Name> {normalize_domain(domain)} "
        f"&& aeo audit-cycle {normalize_domain(domain)} -t <Name>\n"
    )
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
