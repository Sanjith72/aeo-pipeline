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
from functools import lru_cache
from typing import Any

from ..logging import get_logger
from ..nlp import load_prompt
from ..nlp.llm import LLMClient
from ..settings import load_yaml_file
from ..storage.models import ExtractionBundle, PageScore

log = get_logger(__name__)

SKILLS_VERSION = "1.0"

# Fallback per-skill weights when config/scoring.yaml has no `skills:` section.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "messaging": 1.4,
    "conversion": 1.4,
    "discovery_visibility": 1.0,
    "proof_trust": 1.0,
    "structure_ux": 0.8,
}
_DEFAULT_MAX_PRIORITIES = 8
_DEFAULT_LLM_BLEND = 0.6
# CH-14: Discovery points deducted when an engine RAN and did not cite the page. Only ever
# applies to a real 'not_cited' verdict — 'unavailable' (the default, engine off) changes
# nothing, so enabling Perplexity is what turns this on, not a silent score shift.
_DEFAULT_AI_VIS_PENALTY = 10
_LLM_CONTENT_CAP = 2500  # chars of body text handed to the messaging/conversion judge


@lru_cache(maxsize=1)
def _skills_cfg() -> dict[str, Any]:
    """The `skills:` block from scoring.yaml (weights, max_priorities, llm_blend),
    merged over defaults. Cached — config is static for a process lifetime."""
    raw = (load_yaml_file("scoring.yaml") or {}).get("skills", {}) or {}
    weights = {**_DEFAULT_WEIGHTS, **{k: float(v) for k, v in (raw.get("weights") or {}).items()}}
    return {
        "weights": weights,
        "max_priorities": int(raw.get("max_priorities", _DEFAULT_MAX_PRIORITIES)),
        "llm_blend": float(raw.get("llm_blend", _DEFAULT_LLM_BLEND)),
        "ai_visibility_penalty": int(raw.get("ai_visibility_penalty", _DEFAULT_AI_VIS_PENALTY)),
    }

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


def _chunk_text(bundle: ExtractionBundle) -> str:
    chunker = bundle.get("chunker") or {}
    chunks = chunker.get("chunks") or []
    return " ".join(c.get("text", "") for c in chunks).strip()


def _llm_judge(prompt_name: str, llm: LLMClient, replacements: dict[str, str]) -> dict[str, Any] | None:
    """Run a net-new subjective skill (Messaging/Conversion) through the LLM. Returns
    ``{score: 0-100, suggestions: [...]}`` or None on any failure — the caller then keeps
    the deterministic score (the deterministic-first contract: an absent/hung model never
    blocks or floors a skill)."""
    try:
        prompt = load_prompt(prompt_name)
        for token, value in replacements.items():
            prompt = prompt.replace(token, value)
        out = llm.generate_json(prompt)
    except Exception as exc:  # any LLM/parse failure degrades to deterministic
        log.warning("skill_llm_failed", skill=prompt_name, error=str(exc))
        return None
    if not isinstance(out, dict) or not isinstance(out.get("score"), (int, float)):
        return None
    score = max(0, min(100, round(float(out["score"]))))
    suggestions = [str(s).strip() for s in (out.get("suggestions") or []) if str(s).strip()][:3]
    return {"score": score, "suggestions": suggestions}


def _blend(det: dict[str, Any], skill: str, judgement: dict[str, Any] | None) -> dict[str, Any]:
    """Combine the deterministic heuristic with the LLM judgement. LLM present →
    ``hybrid``: score = llm_blend·llm + (1-llm_blend)·deterministic, with the LLM's
    page-specific suggestions (falling back to the deterministic ones if it gave none).
    LLM absent → the deterministic ``provisional`` result, unchanged."""
    if judgement is None:
        return det
    blend = _skills_cfg()["llm_blend"]
    score = round(blend * judgement["score"] + (1 - blend) * det["score"])
    suggestions = [
        {"id": f"sug:{skill}:llm{i}", "text": text, "criterion": None}
        for i, text in enumerate(judgement["suggestions"])
    ] or det["suggestions"]
    return {
        "score": max(0, min(100, score)),
        "confidence": "hybrid",
        "source_criteria": [],
        "suggestions": suggestions[:3],
        "evidence": {**det.get("evidence", {}), "llm_score": judgement["score"]},
    }


def _messaging_skill(bundle: ExtractionBundle, llm: LLMClient | None = None) -> dict[str, Any]:
    """Messaging clarity — is it clear what this is, who it's for, and why it matters?
    Deterministic heuristic over the title/meta/H1 (the ``provisional`` floor), refined by
    an LLM judgement when one is available (``hybrid``, page-specific suggestions)."""
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
    det = _signal_skill("messaging", signals)
    if llm is None or not llm.enabled:
        return det
    headings_flat = "; ".join((headings.get("by_level", {}).get("h2", []) or [])[:8])
    judgement = _llm_judge(
        "messaging_clarity", llm,
        {
            "<<TITLE>>": title[:200],
            "<<META>>": desc[:300],
            "<<H1>>": h1_text[:200],
            "<<HEADINGS>>": headings_flat[:600],
            "<<CONTENT>>": _chunk_text(bundle)[:_LLM_CONTENT_CAP],
        },
    )
    return _blend(det, "messaging", judgement)


def _conversion_skill(bundle: ExtractionBundle, llm: LLMClient | None = None) -> dict[str, Any]:
    """Conversion path — is there an obvious next step, a mid-funnel path, handled
    objections? Deterministic CTA/pricing/mid-funnel/nav heuristics (``provisional``
    floor), refined by an LLM judgement when available (``hybrid``)."""
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
    det = _signal_skill("conversion", signals)
    if llm is None or not llm.enabled:
        return det
    from urllib.parse import urlsplit

    link_paths = "; ".join(dict.fromkeys(urlsplit(u).path for u in internal))[:600]
    meta = bundle.get("meta") or {}
    headings = bundle.get("headings") or {}
    judgement = _llm_judge(
        "conversion_path", llm,
        {
            "<<TITLE>>": (meta.get("title") or "")[:200],
            "<<H1>>": (headings.get("h1_text") or "")[:200],
            "<<LINKS>>": link_paths,
            "<<CONTENT>>": _chunk_text(bundle)[:_LLM_CONTENT_CAP],
        },
    )
    return _blend(det, "conversion", judgement)


def _lift_factor(sug: dict[str, Any], skill: dict[str, Any]) -> float | None:
    """Deterministic predicted-lift factor in 0-1 for one suggestion, or None when it
    cannot be measured.

    This is CH-06's third factor at the SUGGESTION tier: the headroom left on the rubric
    criterion the suggestion targets — ``(5 - tier) / 4``. A criterion already at tier 5 has
    no headroom (0.0); one at tier 1 has all of it (1.0). It is deliberately NOT
    ``validation.predict.predict_lifts``: that simulator re-scores a synthetic bundle per
    recommendation, which is far too heavy to run per suggestion on every scored page.
    Headroom is the same signal the simulator integrates, read directly.

    Returns None for the LLM-judged Messaging/Conversion suggestions, which target no rubric
    criterion — there is nothing deterministic to measure, and inventing a number would make
    the ranking look more grounded than it is. The caller substitutes the mean of the
    measurable factors so those items rank neutrally rather than being pushed up or down.
    """
    criterion = sug.get("criterion")
    if not criterion:
        return None
    tier = (skill.get("evidence") or {}).get("tier_inputs", {}).get(criterion)
    if tier is None:
        return None
    return max(0.0, min(1.0, (5.0 - float(tier)) / 4.0))


def _priorities(skills: dict[str, dict[str, Any]], weights: dict[str, float], limit: int) -> list[dict[str, Any]]:
    """The impact-ranked "fix these first" list (CH-06): every skill's suggestions, scored by
    ``weight × severity × predicted_lift`` and sorted so the highest-impact failures surface
    first — the "50 from 500" cut.

    * ``weight``   — the skill's configured weight (scoring.yaml ``skills.weights``).
    * ``severity`` — the skill's score gap, ``(100 - score)/100``: a skill already near 100
      contributes low-severity items.
    * ``lift``     — deterministic headroom on the targeted rubric criterion (see
      :func:`_lift_factor`). This is what stops a suggestion for an already-strong criterion
      inside a weak skill from outranking a genuinely broken one.

    Each item carries ``lift_basis`` ('headroom' or 'imputed') so a reader can tell a
    measured factor from a substituted one — the ranking is never silently fabricated."""
    raw: list[tuple[dict[str, Any], float | None]] = []
    for name, skill in skills.items():
        weight = weights.get(name, 1.0)
        severity = max(0.0, (100 - skill["score"]) / 100)
        for sug in skill["suggestions"]:
            raw.append(
                (
                    {
                        "skill": name,
                        "text": sug["text"],
                        "criterion": sug.get("criterion"),
                        "skill_score": skill["score"],
                        "_base": weight * severity,
                    },
                    _lift_factor(sug, skill),
                )
            )

    # Impute the unmeasurable (LLM) suggestions at the MEAN of the measured ones, so they
    # rank among them rather than systematically above (factor 1.0) or below (factor 0).
    measured = [f for _, f in raw if f is not None]
    neutral = (sum(measured) / len(measured)) if measured else 1.0

    items: list[dict[str, Any]] = []
    for item, factor in raw:
        lift = neutral if factor is None else factor
        base = item.pop("_base")
        items.append(
            {
                **item,
                "lift": round(lift, 4),
                "lift_basis": "imputed" if factor is None else "headroom",
                "impact": round(base * lift, 4),
            }
        )
    items.sort(key=lambda it: (-it["impact"], it["skill"]))
    return items[:limit]


def _apply_ai_visibility(skill: dict[str, Any], verdict: dict[str, Any] | None, penalty: int) -> dict[str, Any]:
    """Fold the CH-14 AI-snapshot verdict into Discovery & Visibility (CH-04 puts it there).

    Honest by construction, mirroring ``pipeline/ai_visibility``'s three states:
      * ``unavailable`` — the probe did NOT run (engine off/unkeyed is the default). Attach
        it for transparency but change NOTHING: penalising a check we never made would
        invent a failure, and it would silently move every score the moment ops toggles
        Perplexity on.
      * ``cited`` — no fake boost. Being found is the goal, not extra credit; inflating here
        would make the skill score depend on an external engine's mood.
      * ``not_cited`` — a real, measured Discovery failure: a bounded penalty plus a
        concrete suggestion, ranked first because it is the outcome the product sells.
    """
    out = dict(skill)
    out["ai_visibility"] = verdict  # always attached (may be None) so the UI can render it
    if not verdict or verdict.get("status") != "not_cited" or penalty <= 0:
        return out
    out["score"] = max(0, int(skill["score"]) - penalty)
    out["suggestions"] = [
        {
            # `id` is part of the SkillSuggestion contract (web/lib/types.ts) and is the
            # React key the UI renders with — omitting it broke the shape for this one item.
            "id": "sug:discovery_visibility:ai_visibility",
            "criterion": "ai_visibility",
            "text": (
                "AI answer engines don't cite this page yet. Add a direct, quotable answer "
                "to the question buyers actually ask, near the top, in plain sentences."
            ),
        },
        *skill.get("suggestions", []),
    ][:3]
    return out


def build_skill_scores(
    page_score: PageScore,
    bundle: ExtractionBundle,
    *,
    llm: LLMClient | None = None,
    ai_visibility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The CH-04 output for one page: five skills (each 0-100 with suggestions), the
    WEIGHTED overall (CH-06), and an impact-ranked ``priorities`` list. Pass ``llm`` to
    LLM-judge Messaging/Conversion (the deep audit); omit it for the deterministic-only
    free tier (the cost boundary). Pass ``ai_visibility`` (CH-14) to fold the AI-snapshot
    verdict into Discovery & Visibility."""
    cfg = _skills_cfg()
    weights = cfg["weights"]
    skills = {
        "messaging": _messaging_skill(bundle, llm),
        "conversion": _conversion_skill(bundle, llm),
        "discovery_visibility": _apply_ai_visibility(
            _mapped_skill("discovery_visibility", page_score),
            ai_visibility,
            cfg["ai_visibility_penalty"],
        ),
        "proof_trust": _mapped_skill("proof_trust", page_score),
        "structure_ux": _mapped_skill("structure_ux", page_score),
    }
    total_w = sum(weights.get(k, 1.0) for k in skills) or 1.0
    overall = round(sum(s["score"] * weights.get(k, 1.0) for k, s in skills.items()) / total_w)
    return {
        "skills_version": SKILLS_VERSION,
        "overall": overall,
        "skills": skills,
        "priorities": _priorities(skills, weights, cfg["max_priorities"]),
    }
