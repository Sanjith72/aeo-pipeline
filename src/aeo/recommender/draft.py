"""
Content Drafter — turn a missing blueprint node into ready-to-publish page copy.

The Coverage Diff surfaces the pages an ideal site is *missing*; this drafts them.
Each :class:`PageDraft` is a complete page an editor can publish: an H1, a meta
description, an intro, a section per ideal heading / seed question (real headings +
body prose), an FAQ, and valid JSON-LD. The schema markup is built **in code, never
by the model**, so it is always well-formed — mirroring ``recommender/schema.py``.

Deterministic-first, like the rest of the recommender:

  * **No LLM** → a structured *scaffold* draft: headings from the page-type's ideal
    architecture + the node's seed questions, body paragraphs grounded in the node's
    title / required entities / cluster, and full JSON-LD. Clearly stamped
    ``generator="deterministic"`` / ``draft_quality="scaffold"`` — useful as an outline,
    honest about not being final prose.
  * **LLM enabled** → fully articulated body prose for every section and FAQ answer,
    re-assembled into the same shape (``draft_quality="full"``). The model writes the
    words; code builds the HTML and JSON-LD. Bounded (section/length caps) and
    failure-safe: any error or malformed output falls back to the scaffold.

Topic/industry-agnostic — a draft is grounded only in the node and the run's topic, so
it works for healthcare, finance, legal, … exactly as for the original security topic.

Home: the site report's net-new content recommendations (JSONB). Missing pages have no
crawled-page row, so the site report — not the per-page recommendations table — is where
these live. No schema migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..logging import get_logger
from ..nlp.llm import LLMClient
from ..reference import Reference, load_reference

log = get_logger(__name__)

_SCHEMA_CONTEXT = "https://schema.org"
# Page-types whose draft reads as an article (gets Article/TechArticle markup).
_ARTICLE_TYPES = {"pillar", "blog"}
# Bounds on anything the LLM returns (untrusted, cost/abuse guards).
_MAX_SECTIONS = 8
_MAX_FAQ = 6
_MAX_BODY_LEN = 2000
_MAX_HEADING_LEN = 160
_MAX_INTRO_LEN = 600
# Default section skeleton when neither ideal headings nor seed questions exist.
_DEFAULT_HEADINGS = ["What it is", "Why it matters", "How it works"]


@dataclass(slots=True)
class DraftSection:
    heading: str
    body: str  # ready-to-publish prose (markdown/plain), or a grounded scaffold line


@dataclass(slots=True)
class PageDraft:
    """A complete, publishable draft for one missing page."""

    slug: str
    title: str
    h1: str
    meta_description: str
    page_type: str
    intent: str
    sections: list[DraftSection] = field(default_factory=list)
    faq: list[dict[str, str]] = field(default_factory=list)
    jsonld: list[dict[str, Any]] = field(default_factory=list)
    body_markdown: str = ""
    word_count: int = 0
    generator: str = "deterministic"
    draft_quality: str = "scaffold"  # "scaffold" (deterministic) | "full" (LLM prose)

    def to_payload(self) -> dict[str, Any]:
        """Flatten into the JSONB stored under a site-report content brief's ``draft``."""
        return {
            "h1": self.h1,
            "meta_description": self.meta_description,
            "sections": [asdict(s) for s in self.sections],
            "faq": self.faq,
            "jsonld": self.jsonld,
            "body_markdown": self.body_markdown,
            "word_count": self.word_count,
            "generator": self.generator,
            "draft_quality": self.draft_quality,
        }


# ── node access (accepts a MissingNode-like dict or any mapping) ──────────────


def _node_get(node: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(node, Mapping):
        return node.get(key, default)
    return getattr(node, key, default)


def _is_faq_heading(heading: str) -> bool:
    return heading.strip().lower() in {"faq", "faqs", "frequently asked questions"}


def _headings_for(page_type: str, seed_questions: list[str], reference: Reference) -> list[str]:
    """Body-section skeleton: the page-type's structural headings (the FAQ is rendered
    separately from ``seed_questions``, so a literal 'FAQ' heading is dropped). Falls back
    to the seed questions, then a sensible default — deduped, order-stable, bounded."""
    ideal = [h for h in reference.architecture_for(page_type).headings if not _is_faq_heading(h)]
    headings = ideal or seed_questions or list(_DEFAULT_HEADINGS)
    seen: set[str] = set()
    out: list[str] = []
    for h in headings:
        t = str(h).strip()[:_MAX_HEADING_LEN]
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
    return out[:_MAX_SECTIONS]


# ── deterministic scaffold (always available) ─────────────────────────────────


def _scaffold_body(heading: str, title: str, entities: list[str], cluster: str | None) -> str:
    """A grounded scaffold line for a section — real guidance an editor can flesh out,
    never a bare 'TODO'."""
    ent = f" Cover the key concepts: {', '.join(entities)}." if entities else ""
    ctx = f" Tie it back to the {cluster} cluster." if cluster else ""
    return (
        f"Open with a concise, liftable answer (2-4 sentences) addressing \"{heading}\" "
        f"for {title}.{ent}{ctx}"
    )


def _scaffold_draft(
    *, slug: str, title: str, page_type: str, intent: str, headings: list[str],
    seed_questions: list[str], entities: list[str], cluster: str | None, origin: str | None,
) -> PageDraft:
    h1 = title
    meta = f"{title}: a clear, citable overview" + (f" covering {', '.join(entities[:3])}." if entities else ".")
    sections = [DraftSection(heading=h, body=_scaffold_body(h, title, entities, cluster)) for h in headings]
    faq = [
        {"question": q, "answer": f"Answer \"{q}\" directly in 2-4 sentences, naming {title}."}
        for q in seed_questions[:_MAX_FAQ]
    ]
    return _finalize(
        slug=slug, title=title, h1=h1, meta=meta, page_type=page_type, intent=intent,
        sections=sections, faq=faq, intro="", origin=origin,
        generator="deterministic", quality="scaffold",
    )


# ── LLM prose (optional upgrade) ──────────────────────────────────────────────

_DRAFT_SYSTEM = (
    "You are an expert content writer producing a ready-to-publish web page for the "
    "given topic and industry. Write real, specific, publishable prose — not outlines or "
    "instructions. Be accurate and concrete; do not invent statistics or fake citations. "
    "Reply with JSON only."
)


def _draft_prompt(
    *, title: str, topic: str, page_type: str, intent: str, headings: list[str],
    seed_questions: list[str], entities: list[str],
) -> str:
    return (
        f"Topic / industry: {topic}\n"
        f"Page to write: \"{title}\"  (type={page_type}, intent={intent})\n"
        f"Required entities/standards to cover: {', '.join(entities) or '(none specified)'}\n"
        f"Real questions this page must answer: {', '.join(seed_questions) or '(infer the obvious ones)'}\n\n"
        f"Write the page as JSON with keys:\n"
        f'  "meta_description": a <=160-char SEO description;\n'
        f'  "intro": a 2-4 sentence liftable opening answer;\n'
        f'  "sections": an array (use these headings, in order: {headings}) of '
        '{"heading","body"} where body is 1-3 finished paragraphs of real prose;\n'
        '  "faq": an array of {"question","answer"} with concise 2-4 sentence answers.\n'
        "Produce the actual copy. Reply JSON only."
    )


def _llm_draft(
    *, slug: str, title: str, topic: str, page_type: str, intent: str, headings: list[str],
    seed_questions: list[str], entities: list[str], cluster: str | None, origin: str | None,
    llm: LLMClient,
) -> PageDraft | None:
    """Ask the model for finished prose, validate/bound it, assemble a PageDraft. Returns
    ``None`` on any failure so the caller falls back to the scaffold."""
    try:
        data = llm.generate_json(
            _draft_prompt(title=title, topic=topic, page_type=page_type, intent=intent,
                          headings=headings, seed_questions=seed_questions, entities=entities),
            _DRAFT_SYSTEM,
        )
    except Exception as exc:  # never let drafting break the report
        log.warning("page_draft_llm_failed", slug=slug, error=str(exc))
        return None
    if not isinstance(data, dict):
        return None

    raw_sections = data.get("sections")
    sections: list[DraftSection] = []
    for s in (raw_sections or [])[:_MAX_SECTIONS]:
        if not isinstance(s, dict):
            continue
        heading = str(s.get("heading", "")).strip()[:_MAX_HEADING_LEN]
        body = str(s.get("body", "")).strip()[:_MAX_BODY_LEN]
        if heading and body:
            sections.append(DraftSection(heading=heading, body=body))
    if not sections:  # no usable prose → let the scaffold handle it
        return None

    faq: list[dict[str, str]] = []
    for f in (data.get("faq") or [])[:_MAX_FAQ]:
        if not isinstance(f, dict):
            continue
        q = str(f.get("question", "")).strip()[:_MAX_HEADING_LEN]
        a = str(f.get("answer", "")).strip()[:_MAX_BODY_LEN]
        if q and a:
            faq.append({"question": q, "answer": a})

    meta = str(data.get("meta_description", "")).strip()[:_MAX_HEADING_LEN] or f"{title}: a clear, citable overview."
    intro = str(data.get("intro", "")).strip()[:_MAX_INTRO_LEN]
    return _finalize(
        slug=slug, title=title, h1=title, meta=meta, page_type=page_type, intent=intent,
        sections=sections, faq=faq, intro=intro, origin=origin,
        generator=llm.model, quality="full",
    )


# ── assembly: JSON-LD (in code) + markdown body ──────────────────────────────


def _origin(url_or_origin: str | None) -> str:
    if not url_or_origin:
        return ""
    parts = urlsplit(url_or_origin)
    if parts.netloc:
        return f"{parts.scheme or 'https'}://{parts.netloc}"
    # A bare domain like "acme.com"
    bare = url_or_origin.strip().strip("/")
    return f"https://{bare}" if bare and "." in bare else ""


def _humanize(segment: str) -> str:
    stem = segment.split(".")[0]
    return stem.replace("-", " ").replace("_", " ").strip().title() or segment


def _jsonld_blocks(
    *, slug: str, title: str, meta: str, page_type: str, faq: list[dict[str, str]], origin: str,
) -> list[dict[str, Any]]:
    """Build valid JSON-LD for the drafted page — deterministically, in code."""
    url = f"{origin}{slug}" if origin else ""
    blocks: list[dict[str, Any]] = []

    main_type = "TechArticle" if page_type == "pillar" else ("Article" if page_type in _ARTICLE_TYPES else "WebPage")
    primary: dict[str, Any] = {"@context": _SCHEMA_CONTEXT, "@type": main_type, "name": title}
    if main_type in {"Article", "TechArticle"}:
        primary["headline"] = title
    if meta:
        primary["description"] = meta
    if url:
        primary["url"] = url
    blocks.append(primary)

    if faq:
        blocks.append({
            "@context": _SCHEMA_CONTEXT,
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f["question"],
                 "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}}
                for f in faq
            ],
        })

    segments = [s for s in slug.split("/") if s]
    if origin and len(segments) >= 2:
        items = [{"@type": "ListItem", "position": 1, "name": "Home", "item": f"{origin}/"}]
        for i, seg in enumerate(segments, start=1):
            items.append({
                "@type": "ListItem", "position": i + 1, "name": _humanize(seg),
                "item": f"{origin}/" + "/".join(segments[:i]),
            })
        blocks.append({"@context": _SCHEMA_CONTEXT, "@type": "BreadcrumbList", "itemListElement": items})

    return blocks


def _markdown(h1: str, intro: str, sections: list[DraftSection], faq: list[dict[str, str]]) -> str:
    lines = [f"# {h1}", ""]
    if intro:
        lines += [intro, ""]
    for s in sections:
        lines += [f"## {s.heading}", "", s.body, ""]
    if faq:
        lines += ["## Frequently Asked Questions", ""]
        for f in faq:
            lines += [f"### {f['question']}", "", f["answer"], ""]
    return "\n".join(lines).strip() + "\n"


def _finalize(
    *, slug: str, title: str, h1: str, meta: str, page_type: str, intent: str,
    sections: list[DraftSection], faq: list[dict[str, str]], intro: str, origin: str | None,
    generator: str, quality: str,
) -> PageDraft:
    org = _origin(origin)
    body_md = _markdown(h1, intro, sections, faq)
    jsonld = _jsonld_blocks(slug=slug, title=title, meta=meta, page_type=page_type, faq=faq, origin=org)
    return PageDraft(
        slug=slug, title=title, h1=h1, meta_description=meta, page_type=page_type, intent=intent,
        sections=sections, faq=faq, jsonld=jsonld, body_markdown=body_md,
        word_count=len(body_md.split()), generator=generator, draft_quality=quality,
    )


# ── public API ────────────────────────────────────────────────────────────────


def draft_missing_page(
    node: Mapping[str, Any] | Any,
    *,
    topic: str,
    llm: LLMClient | None = None,
    reference: Reference | None = None,
    origin: str | None = None,
) -> PageDraft:
    """Draft one missing page from a blueprint node (a ``MissingNode`` or a brief dict).

    Returns a publishable :class:`PageDraft`: LLM-authored prose when a model is enabled,
    a grounded deterministic scaffold otherwise. JSON-LD is always built in code, so it is
    valid regardless of which path produced the prose."""
    reference = reference or load_reference()
    slug = str(_node_get(node, "slug", "/"))
    title = str(_node_get(node, "title") or _humanize(slug.strip("/")) or topic)
    page_type = str(_node_get(node, "page_type", "default"))
    intent = str(_node_get(node, "intent", "informational"))
    cluster = _node_get(node, "cluster")
    entities = [str(e) for e in (_node_get(node, "required_entities") or [])]
    seed_questions = [str(q) for q in (_node_get(node, "seed_questions") or [])]
    headings = _headings_for(page_type, seed_questions, reference)

    if llm is not None and getattr(llm, "enabled", False):
        draft = _llm_draft(
            slug=slug, title=title, topic=topic, page_type=page_type, intent=intent,
            headings=headings, seed_questions=seed_questions, entities=entities,
            cluster=cluster, origin=origin, llm=llm,
        )
        if draft is not None:
            log.info("page_draft_generated", slug=slug, generator=draft.generator, words=draft.word_count)
            return draft

    return _scaffold_draft(
        slug=slug, title=title, page_type=page_type, intent=intent, headings=headings,
        seed_questions=seed_questions, entities=entities, cluster=cluster, origin=origin,
    )


def draft_site_pages(
    briefs: list[dict[str, Any]],
    *,
    topic: str,
    llm: LLMClient | None = None,
    reference: Reference | None = None,
    origin: str | None = None,
    limit: int = 10,
    perplexity: Any = None,
) -> list[dict[str, Any]]:
    """Attach a ready-to-publish ``draft`` to the highest-priority missing-page briefs.

    Briefs are assumed already priority-ordered (the site builder sorts them). Only the
    top ``limit`` are drafted — drafting is the expensive, LLM-backed step, and the long
    tail stays as lightweight briefs. Each draft is then validated (Independent Validator
    + citation-signal check) and the verdict attached under ``draft["validation"]``; pass
    ``perplexity`` to also run the real-world citation probe. Returns the same brief list,
    enriched in place."""
    if limit <= 0 or not briefs:
        return briefs
    reference = reference or load_reference()
    # Local import: pulls in the validation block (extractors + independent auditor),
    # which the lightweight brief-only path must not require. Runtime-only, so no
    # import cycle with validation -> recommender.
    from ..validation.draft_check import validate_page_draft

    for brief in briefs[:limit]:
        try:
            draft = draft_missing_page(brief, topic=topic, llm=llm, reference=reference, origin=origin)
            payload = draft.to_payload()
            # Full LLM-authored pages (invented prose + JSON-LD) must pass the
            # non-circular Independent Validator + citation-signal check before they
            # ship -- nothing else re-scores them.
            payload["validation"] = validate_page_draft(
                payload, url=origin, perplexity=perplexity
            )
            brief["draft"] = payload
        except Exception as exc:  # one bad draft never sinks the report
            log.warning("page_draft_skipped", slug=brief.get("slug"), error=str(exc))
    return briefs
