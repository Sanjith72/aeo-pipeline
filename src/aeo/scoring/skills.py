"""
The 5-skill derived scoring layer (v5 CH-04; contract: docs/V5_CONTRACTS.md §a).

Maps the existing 10-criterion rubric (1-5 tiers) onto the five outcome skills —
Messaging, Conversion, Discovery & Visibility, Proof & Trust, Structure & UX — each
0-100 with 2-3 concrete suggestions. Strictly a layer OVER :mod:`aeo.scoring`:
``rubric_scores_v2``, the 10-key ``SCORERS`` contract, and ``RUBRIC_VERSION`` are
untouched, and every skill row is recomputable from criterion tiers + evidence.

Messaging and Conversion have no rubric ancestor. In this P1 slice they are scored by
deterministic heuristics over the extraction bundle (title/meta/H1 shape; CTA, pricing
and mid-funnel link presence) and honestly labelled ``confidence: "provisional"`` —
the LLM-judged versions land in P2 as parallel scorers. LLM-down must never floor a
skill: the worst honest outcome is a provisional/neutral score.

Pure functions; no network, no DB, no LLM.
"""

from __future__ import annotations

import re
from typing import Any

from ..storage.models import ExtractionBundle, PageScore

SKILLS_VERSION = "1.0"

SKILL_KEYS = (
    "messaging",
    "conversion",
    "discovery_visibility",
    "proof_trust",
    "structure_ux",
)

# Criterion → skill map (locked in docs/V5_CONTRACTS.md). Messaging/Conversion are
# net-new (empty here) until the P2 LLM scorers register their criteria.
SKILL_SOURCES: dict[str, tuple[str, ...]] = {
    "messaging": (),
    "conversion": (),
    "discovery_visibility": ("schema_markup", "qa_blocks", "heading_structure", "entity_consistency"),
    "proof_trust": ("citation_signals", "stats_in_html"),
    "structure_ux": ("answer_readability", "load_speed", "render_accessibility", "content_depth"),
}

# One plain-English fix per criterion, surfaced when that criterion drags its skill down.
# Deliberately outcome language (what to do), not rubric language (what we measured).
_CRITERION_SUGGESTIONS: dict[str, str] = {
    "schema_markup": "Add JSON-LD structured data (Organization plus FAQ/Service) so answer engines can read what you offer.",
    "qa_blocks": "Add a short FAQ: 3–5 real customer questions as headings, each answered in 2–3 sentences.",
    "heading_structure": "Give the page one clear H1 and question-style H2s so each section answers one thing.",
    "entity_consistency": "Name your business, product, and category the same way in the title, headings, and body.",
    "citation_signals": "Cite and link recognizable sources, and add an author/company byline so claims are attributable.",
    "stats_in_html": "Put concrete numbers — results, counts, dates — in the HTML text, not inside images.",
    "answer_readability": "Open each section with a direct 1–2 sentence answer before the detail.",
    "load_speed": "Lighten the page (compress images, defer scripts) — slow pages get skipped by crawlers and people.",
    "render_accessibility": "Serve the key content as real HTML — content that only appears after JavaScript is invisible to most engines.",
    "content_depth": "Deepen thin sections: cover the follow-up questions a buyer would ask next.",
}

_CTA_PATH_RE = re.compile(
    r"/(contact|book|booking|quote|demo|schedule|get-started|getstarted|start|signup|sign-up|trial|buy|order)(/|$|[?#.])",
    re.I,
)
_PRICING_PATH_RE = re.compile(r"/(pricing|prices|plans|rates|packages)(/|$|[?#.])", re.I)
_MIDFUNNEL_PATH_RE = re.compile(
    r"/(about|case-stud(?:y|ies)|customers|testimonials|reviews|results|work|portfolio|examples|how-it-works)(/|$|[?#.-])",
    re.I,
)


def _tier_to_100(tier: float) -> int:
    """1-5 tier → 0-100 (1 → 0, 5 → 100)."""
    return round(max(0.0, min(1.0, (tier - 1.0) / 4.0)) * 100)


def _mapped_skill(
    skill: str, page_score: PageScore, *, max_suggestions: int = 3
) -> dict[str, Any]:
    """Score a rubric-mapped skill: mean of its source tiers rescaled to 0-100, with
    suggestions drawn from the weakest source criteria (weight × severity ordering
    arrives with CH-06; for now lowest tier first)."""
    sources = SKILL_SOURCES[skill]
    crits = [page_score.criteria[name] for name in sources if name in page_score.criteria]
    if not crits:  # defensive — the SCORERS contract makes this unreachable
        return _neutral_skill(skill, reason="no source criteria scored")

    tiers = {c.name: c.value for c in crits}
    score = _tier_to_100(sum(tiers.values()) / len(tiers))
    # A skill scored partly by the LLM is 'hybrid'; a scorer error still yields the
    # deterministic floor tier, which stays honest as 'deterministic'.
    confidence = (
        "hybrid"
        if any(c.scored_by not in ("deterministic", "error") for c in crits)
        else "deterministic"
    )
    weakest = sorted(crits, key=lambda c: (c.value, c.name))
    suggestions = [
        {
            "id": f"sug:{skill}:{c.name}",
            "text": _CRITERION_SUGGESTIONS.get(c.name, c.notes or c.name),
            "criterion": c.name,
        }
        for c in weakest
        if c.value < 5
    ][:max_suggestions]
    return {
        "score": score,
        "confidence": confidence,
        "source_criteria": list(sources),
        "suggestions": suggestions,
        "evidence": {"tier_inputs": tiers},
    }


def _neutral_skill(skill: str, *, reason: str) -> dict[str, Any]:
    """The honest can't-judge outcome: mid score, labelled neutral — never a fake 0."""
    return {
        "score": 50,
        "confidence": "neutral",
        "source_criteria": list(SKILL_SOURCES[skill]),
        "suggestions": [],
        "evidence": {"reason": reason},
    }


def _signal_skill(skill: str, signals: list[tuple[str, int, bool, str]]) -> dict[str, Any]:
    """Score a heuristic skill from (name, points, passed, fix-text) signals: the score is
    the points earned, the suggestions are the failed signals' fixes (largest miss first)."""
    earned = sum(points for _, points, passed, _ in signals if passed)
    total = sum(points for _, points, _, _ in signals)
    failed = sorted(
        (s for s in signals if not s[2]), key=lambda s: -s[1]
    )
    suggestions = [
        {"id": f"sug:{skill}:{name}", "text": fix, "criterion": None}
        for name, _, _, fix in failed
    ][:3]
    return {
        "score": round(earned / total * 100) if total else 50,
        "confidence": "provisional",
        "source_criteria": [],
        "suggestions": suggestions,
        "evidence": {"signals": {name: passed for name, _, passed, _ in signals}},
    }


def _messaging_skill(bundle: ExtractionBundle) -> dict[str, Any]:
    """P1 heuristic: is the page's self-description shaped like a clear pitch? Judges the
    title tag, meta description, and H1 — the three places a visitor (or an answer
    engine) learns what this is, who it's for, and why it matters."""
    meta = bundle.get("meta") or {}
    headings = bundle.get("headings") or {}
    title = (meta.get("title") or "").strip()
    desc = (meta.get("description") or "").strip()
    h1_text = (headings.get("h1_text") or "").strip()
    h1_count = int(headings.get("h1_count") or 0)

    signals = [
        (
            "title",
            25,
            15 <= len(title) <= 70,
            "Write a title tag that says what you do and who it's for (15–70 characters).",
        ),
        (
            "description",
            25,
            50 <= len(desc) <= 170,
            "Add a meta description that states your offer and its outcome in one or two sentences.",
        ),
        (
            "single_h1",
            25,
            h1_count == 1 and not bool(headings.get("template_h1")),
            "Give the page exactly one H1 that names what you offer — competing or boilerplate H1s blur the message.",
        ),
        (
            "h1_clarity",
            25,
            8 <= len(h1_text) <= 90,
            "Rewrite the H1 as a plain-language statement of what you do (a phrase, not a slogan or a wall of text).",
        ),
    ]
    return _signal_skill("messaging", signals)


def _conversion_skill(bundle: ExtractionBundle) -> dict[str, Any]:
    """P1 heuristic: does the page offer a next step? Looks for a primary CTA path
    (contact/demo/book), findable pricing, a mid-funnel path for undecided visitors,
    and basic internal navigation."""
    links = bundle.get("links") or {}
    internal: list[str] = list(links.get("internal") or [])
    internal_count = int(links.get("internal_count") or len(internal))

    has_cta = any(_CTA_PATH_RE.search(u) for u in internal)
    has_pricing = any(_PRICING_PATH_RE.search(u) for u in internal)
    has_midfunnel = any(_MIDFUNNEL_PATH_RE.search(u) for u in internal)

    signals = [
        (
            "primary_cta",
            35,
            has_cta,
            "Add one clear primary call-to-action (contact, book, or demo) a visitor can act on from this page.",
        ),
        (
            "pricing_findable",
            25,
            has_pricing,
            "Make pricing — or at least 'how pricing works' — findable from the homepage; hidden pricing stalls buyers.",
        ),
        (
            "mid_funnel_path",
            25,
            has_midfunnel,
            "Give undecided visitors a middle step: case studies, customer examples, or a substantial about page.",
        ),
        (
            "navigation",
            15,
            internal_count >= 5,
            "Link your key pages from here so visitors (and engines) can reach the next step.",
        ),
    ]
    return _signal_skill("conversion", signals)


def build_skill_scores(page_score: PageScore, bundle: ExtractionBundle) -> dict[str, Any]:
    """The locked CH-04 output for one page: five skills, each 0-100 with suggestions,
    plus the equal-weight overall (per-skill weights arrive with CH-06)."""
    skills = {
        "messaging": _messaging_skill(bundle),
        "conversion": _conversion_skill(bundle),
        "discovery_visibility": _mapped_skill("discovery_visibility", page_score),
        "proof_trust": _mapped_skill("proof_trust", page_score),
        "structure_ux": _mapped_skill("structure_ux", page_score),
    }
    overall = round(sum(s["score"] for s in skills.values()) / len(skills))
    return {"skills_version": SKILLS_VERSION, "overall": overall, "skills": skills}
