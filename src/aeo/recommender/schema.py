"""
Schema recommender — DETERMINISTIC JSON-LD generation (no LLM).

Builds the structured data a page is missing directly from already-extracted
content. Answer engines lean heavily on JSON-LD, so emitting valid markup the
page lacks is the highest-confidence, zero-hallucination recommendation we make.

Covers four high-value types, proposed only when genuinely missing and supported
by real content:

  * **FAQPage** — when the body has detected Q&A pairs but no FAQPage block.
  * **Article / TechArticle** — when there's an author/date byline but no
    article-type block.
  * **Organization** — when the page's primary entity is known but unmarked.
  * **BreadcrumbList** — when the URL has a real path hierarchy and no breadcrumb.

Each recommendation carries the concrete JSON-LD object in its payload, ready for
a human (or a later automated step) to paste into the page head.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..storage.models import ExtractionBundle
from .models import SCHEMA, Recommendation

_ARTICLE_TYPES = {"Article", "TechArticle", "NewsArticle", "BlogPosting"}
_SCHEMA_CONTEXT = "https://schema.org"
_MAX_FAQ = 10


def recommend_schema(bundle: ExtractionBundle, *, url: str) -> list[Recommendation]:
    schema = bundle.get("schema_jsonld", {}) or {}
    present = set(schema.get("types", []) or [])
    origin = _origin(url)

    recs: list[Recommendation] = []

    faq = _faq_page(bundle, present)
    if faq is not None:
        recs.append(faq)

    article = _article(bundle, present, url)
    if article is not None:
        recs.append(article)

    org = _organization(bundle, present, origin)
    if org is not None:
        recs.append(org)

    crumb = _breadcrumb(present, url, origin)
    if crumb is not None:
        recs.append(crumb)

    return recs


def _faq_page(bundle: ExtractionBundle, present: set[str]) -> Recommendation | None:
    if "FAQPage" in present:
        return None
    qa = bundle.get("qa_blocks", {}) or {}
    pairs = qa.get("qa_pairs", []) or []
    if not pairs:
        return None

    main_entity = [
        {
            "@type": "Question",
            "name": p.get("question", "").rstrip(),
            "acceptedAnswer": {"@type": "Answer", "text": p.get("answer_preview", "")},
        }
        for p in pairs[:_MAX_FAQ]
    ]
    jsonld = {"@context": _SCHEMA_CONTEXT, "@type": "FAQPage", "mainEntity": main_entity}
    n = len(main_entity)
    return Recommendation(
        rec_type=SCHEMA,
        criterion="schema_markup",
        title=f"Add FAQPage JSON-LD ({n} Q&A pair{'s' if n != 1 else ''})",
        rationale=(
            f"The body has {n} question/answer pair{'s' if n != 1 else ''} but no FAQPage "
            "structured data. Answer engines surface FAQ schema directly in results."
        ),
        current_state=(
            f"The page has {n} Q&A pair{'s' if n != 1 else ''} in the body but no FAQPage "
            "structured data, so engines can't lift them as answers."
        ),
        action_required=f"Add a FAQPage JSON-LD block covering the {n} existing Q&A pair{'s' if n != 1 else ''}.",
        how_to=(
            "Paste the generated FAQPage JSON-LD into the page <head>, then validate with "
            "Google's Rich Results Test."
        ),
        payload={"schema_type": "FAQPage", "jsonld": jsonld},
    )


def _article(bundle: ExtractionBundle, present: set[str], url: str) -> Recommendation | None:
    if present & _ARTICLE_TYPES:
        return None
    eeat = bundle.get("eeat", {}) or {}
    meta = bundle.get("meta", {}) or {}
    author = eeat.get("author")
    date = eeat.get("publish_date")
    # Only propose an Article when there's a real byline signal — avoids tagging
    # product/landing pages as articles.
    if not (author or date):
        return None

    headline = meta.get("title") or (bundle.get("headings", {}) or {}).get("h1_text") or ""
    jsonld: dict[str, Any] = {"@context": _SCHEMA_CONTEXT, "@type": "Article", "url": url}
    if headline:
        jsonld["headline"] = headline
    if meta.get("description"):
        jsonld["description"] = meta["description"]
    if author:
        jsonld["author"] = {"@type": "Person", "name": author}
    if date:
        jsonld["datePublished"] = date

    return Recommendation(
        rec_type=SCHEMA,
        criterion="schema_markup",
        title="Add Article JSON-LD",
        rationale=(
            "The page reads as an article (byline/date present) but carries no Article "
            "structured data, so engines can't attribute author or freshness."
        ),
        current_state=(
            "The page has a byline/date but no Article structured data, so engines can't "
            "attribute its author or freshness."
        ),
        action_required="Add an Article JSON-LD block with the author and publish date.",
        how_to=(
            "Insert the generated Article JSON-LD into the page <head> and confirm author/date "
            "resolve in the Rich Results Test."
        ),
        payload={"schema_type": "Article", "jsonld": jsonld},
    )


def _organization(bundle: ExtractionBundle, present: set[str], origin: str) -> Recommendation | None:
    if "Organization" in present:
        return None
    entities = bundle.get("entities", {}) or {}
    primary = entities.get("primary") or {}
    name = primary.get("name")
    if not name:
        return None

    jsonld = {"@context": _SCHEMA_CONTEXT, "@type": "Organization", "name": name}
    if origin:
        jsonld["url"] = origin

    return Recommendation(
        rec_type=SCHEMA,
        criterion="schema_markup",
        title=f"Add Organization JSON-LD ({name})",
        rationale=(
            f"'{name}' is the page's primary entity but has no Organization markup, "
            "weakening entity recognition by answer engines."
        ),
        current_state=f"'{name}' is the page's primary entity but carries no Organization markup.",
        action_required=f"Add an Organization JSON-LD block naming '{name}'.",
        how_to=(
            "Add the generated Organization JSON-LD to the site-wide <head> (or homepage) so "
            "engines bind the brand entity consistently."
        ),
        payload={"schema_type": "Organization", "jsonld": jsonld},
    )


def _breadcrumb(present: set[str], url: str, origin: str) -> Recommendation | None:
    if "BreadcrumbList" in present:
        return None
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if len(segments) < 2 or not origin:
        return None

    items: list[dict[str, Any]] = [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{origin}/"}
    ]
    for i, seg in enumerate(segments, start=1):
        crumb_url = f"{origin}/" + "/".join(segments[:i])
        items.append(
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": _humanize(seg),
                "item": crumb_url,
            }
        )

    jsonld = {"@context": _SCHEMA_CONTEXT, "@type": "BreadcrumbList", "itemListElement": items}
    return Recommendation(
        rec_type=SCHEMA,
        criterion="schema_markup",
        title="Add BreadcrumbList JSON-LD",
        rationale=(
            "The URL has a clear section hierarchy but no BreadcrumbList markup; "
            "breadcrumbs help engines place the page in site context."
        ),
        current_state="The URL has a clear section hierarchy but no BreadcrumbList markup.",
        action_required="Add a BreadcrumbList JSON-LD block reflecting the URL path.",
        how_to="Insert the generated BreadcrumbList JSON-LD into the page <head>.",
        payload={"schema_type": "BreadcrumbList", "jsonld": jsonld},
    )


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.netloc:
        return ""
    scheme = parts.scheme or "https"
    return f"{scheme}://{parts.netloc}"


def _humanize(segment: str) -> str:
    stem = segment.split(".")[0]  # drop a trailing .html/.php extension
    return stem.replace("-", " ").replace("_", " ").strip().title() or segment
