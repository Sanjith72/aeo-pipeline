"""
Draft validation — close the Block-4 gap for LLM-authored *draft* content.

Two kinds of draft were shipping into the deliverable without ever passing back
through the validation loop:

  * the per-criterion ``payload["draft"]`` on existing-page content recommendations
    (ready-to-publish replacement copy for one section), and
  * the full :class:`~aeo.recommender.draft.PageDraft` for *missing* pages (a whole
    LLM-authored page: invented body prose + FAQ + JSON-LD).

Both are the highest-hallucination-risk content in the system, yet
``simulate.apply_recommendation`` only ever re-scored the optimistic ``edits`` list,
and the missing-page drafts went straight into the site report unscored. This module
supplies the two seams that fix that:

  * :func:`signals_from_draft` renders a draft (raw HTML, or a lightweight Markdown
    subset) and runs the *same* deterministic extractor suite the crawler uses, so
    ``simulate`` can re-score a recommendation on the content that would **actually
    ship**, not an applier that assumes the target tier was reached.
  * :func:`validate_page_draft` routes a full ``PageDraft`` through the non-circular
    Independent Validator (lead-answer / H1-is-question / valid-JSON-LD) *and* the
    adversarial citation-signal check, so fabricated body prose or invented citations
    are caught before the draft lands in the report.

Pure and deterministic by default (no network); the Perplexity citation probe and
HEAD reachability checks run only when their clients are supplied + enabled.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from ..extract import DEFAULT_EXTRACTORS
from ..storage.models import ExtractionBundle
from ..utils.html import parse
from .adversarial import check_citation_signals, extract_citation_urls
from .independent import DEFAULT_TLDR_MAX_WORDS, validate_independent

# ── draft -> HTML -> extraction signals ───────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# A draft already carrying block-level HTML is passed through untouched.
_HTML_HINT_RE = re.compile(r"<(h[1-6]|p|ul|ol|li|div|section|article|table)\b", re.I)


def _markdown_to_html(text: str) -> str:
    """Convert the Markdown subset our drafts emit (ATX headings + blank-line
    separated paragraphs) into HTML so the structural extractors see real
    ``<h2>``/``<p>`` nodes. Text that already looks like HTML is returned as-is."""
    if _HTML_HINT_RE.search(text):
        return text
    html_parts: list[str] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            html_parts.append("<p>" + " ".join(para).strip() + "</p>")
            para.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            continue
        m = _HEADING_RE.match(line.strip())
        if m:
            flush_para()
            level = len(m.group(1))
            html_parts.append(f"<h{level}>{m.group(2).strip()}</h{level}>")
        else:
            para.append(line.strip())
    flush_para()
    return "\n".join(html_parts)


def signals_from_html(html: str, url: str = "") -> dict[str, dict[str, Any]]:
    """Run the registered deterministic extractors over a raw HTML string and
    return the section -> signals map (the same shape as an ``ExtractionBundle``'s
    ``data``). Mirrors :class:`~aeo.pipeline.stages.ExtractStage`: a *fresh* soup per
    extractor, because ``body_text`` decomposes nodes destructively."""
    out: dict[str, dict[str, Any]] = {}
    for name, fn in DEFAULT_EXTRACTORS:
        try:
            out[name] = fn(html, parse(html), url)
        except Exception:  # a bad extractor must never break re-scoring
            out[name] = {}
    return out


def signals_from_draft(draft: str, url: str = "") -> dict[str, dict[str, Any]]:
    """Measured extraction signals for a draft fragment (HTML or Markdown)."""
    return signals_from_html(_markdown_to_html(draft or ""), url)


# ── full PageDraft -> page HTML -> independent + citation validation ──────────


def render_page_draft_html(payload: dict[str, Any]) -> str:
    """Reassemble a :meth:`PageDraft.to_payload` dict into a single HTML document:
    the Markdown body (intro + sections + FAQ) plus the code-built JSON-LD blocks
    as ``<script type="application/ld+json">``. This is what the extractors — and
    therefore the Independent Validator — see."""
    import json

    body_html = _markdown_to_html(str(payload.get("body_markdown", "")))
    scripts: list[str] = []
    for block in payload.get("jsonld", []) or []:
        try:
            scripts.append(
                '<script type="application/ld+json">'
                + json.dumps(block)
                + "</script>"
            )
        except (TypeError, ValueError):
            continue
    return f"<html><body>{''.join(scripts)}{body_html}</body></html>"


def validate_page_draft(
    payload: dict[str, Any],
    *,
    url: str | None = None,
    perplexity: Any = None,
    verify_reachability: bool = False,
    tldr_max_words: int = DEFAULT_TLDR_MAX_WORDS,
) -> dict[str, Any]:
    """Validate a full missing-page draft before it lands in the site report.

    Runs the non-circular Independent Validator over the rendered page (lead-answer
    liftable, H1 is a question, valid JSON-LD) and the deterministic citation-signal
    check over the body prose — JSON-LD is code-built and safe, but LLM prose can
    invent citations. ``passed`` is the conjunction: independent deterministic checks
    pass **and** no cited URL is hallucinated. Never raises."""
    html = render_page_draft_html(payload)
    bundle = ExtractionBundle(page_id=0)
    for name, data in signals_from_html(html, url or "").items():
        bundle.put(name, data)

    verdict = validate_independent(
        bundle, url=url or "", perplexity=perplexity, tldr_max_words=tldr_max_words
    )
    citations = check_citation_signals(
        extract_citation_urls(str(payload.get("body_markdown", ""))),
        verify_reachability=verify_reachability,
    )
    hallucinated = [c for c in citations if c.hallucinated]

    return {
        "passed": verdict.passed and not hallucinated,
        "independent": verdict.to_detail(),
        "citation_signals": [asdict(c) for c in citations],
        "hallucinated_citations": len(hallucinated),
    }
