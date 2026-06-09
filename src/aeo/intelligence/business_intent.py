"""
Business-Model / User-Intent Engine.

The meeting asked the pipeline to model *who the user is*, not just *what the site
contains*. This engine classifies the site's business model — lead-gen, e-commerce,
local, SaaS, agency, publisher, or enterprise — because the right AEO strategy
differs sharply by model (a SaaS needs pricing/integrations/docs; a local business
needs locations/hours/booking; a publisher lives or dies on content breadth).

Deterministic-first: each model has weighted slug signals; the model with the
highest summed signal wins, with the onboarding industry/topic text nudging the
score. The optional :class:`LLMClient` is used *only* as a tiebreak when the top two
models are within a configurable margin — and even then a complete, correct answer
is always produced with the LLM off (the conservative ``LEAD_GEN`` default when no
signal fires). The LLM never gates; it only disambiguates a genuine tie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..nlp.llm import LLMClient
from .classification import SiteClass
from .config import IntelligenceCfg, load_intelligence_cfg
from .signals import PageView, token_hit


class BusinessModel(StrEnum):
    LEAD_GEN = "lead_gen"
    ECOMMERCE = "ecommerce"
    LOCAL = "local"
    SAAS = "saas"
    AGENCY = "agency"
    PUBLISHER = "publisher"
    ENTERPRISE = "enterprise"


_INDUSTRY_BOOST = 2.0  # fixed nudge per business model whose industry keywords match
# Valid model labels — a user-tuned intelligence.yaml may carry a typo'd key
# (e.g. "e_commerce"); we ignore any signal/hint key that isn't a real BusinessModel
# rather than letting it crash the final BusinessModel(winner) construction.
_VALID_MODELS: frozenset[str] = frozenset(m.value for m in BusinessModel)


@dataclass(slots=True)
class BusinessIntent:
    model: BusinessModel
    confidence: float  # top_score / sum(scores), 0..1
    evidence: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    decided_by: str = "deterministic"  # "deterministic" | "llm-tiebreak" | "default"


def _score_models(
    pages: list[PageView],
    *,
    site_class: SiteClass,
    cfg: IntelligenceCfg,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Sum each model's signal weights (presence-based) over the discovered paths."""
    paths = [p.path for p in pages]
    total = max(1, len(pages))
    blog_ratio = sum(1 for p in pages if p.page_type == "blog") / total

    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for model, sig in cfg.model_signals.items():
        if model not in _VALID_MODELS:  # ignore a typo'd YAML key, don't crash later
            continue
        tokens: dict[str, Any] = sig.get("slug_tokens", {}) or {}
        ev: list[str] = []
        score = 0.0
        for tok, weight in tokens.items():
            if any(token_hit(p, tok) for p in paths):
                score += float(weight)
                ev.append(tok)
        # Publisher: a blog-heavy page mix is itself a strong signal.
        ratio_weight = float(sig.get("blog_ratio_weight", 0) or 0)
        if ratio_weight and blog_ratio >= float(sig.get("blog_ratio_threshold", 0.4)):
            score += ratio_weight
            ev.append(f"blog_ratio={blog_ratio:.2f}")
        # Enterprise: lean on the classified tier.
        bonus_map = sig.get("site_class_bonus", {}) or {}
        if site_class.value in bonus_map:
            score += float(bonus_map[site_class.value])
            ev.append(f"site_class={site_class.value}")
        scores[model] = score
        evidence[model] = ev
    return scores, evidence


def _apply_industry_hints(
    scores: dict[str, float],
    evidence: dict[str, list[str]],
    *,
    topic: str | None,
    category: str | None,
    cfg: IntelligenceCfg,
) -> None:
    hint_text = " ".join(t for t in (topic or "", category or "") if t).lower()
    if not hint_text:
        return
    for model, keywords in cfg.industry_hints.items():
        if model not in _VALID_MODELS:  # ignore a typo'd YAML key
            continue
        if any(kw.lower() in hint_text for kw in keywords):
            scores[model] = scores.get(model, 0.0) + _INDUSTRY_BOOST
            evidence.setdefault(model, []).append(f"industry:{hint_text.strip()}")


def _tied_candidates(scores: dict[str, float], margin: float) -> list[str]:
    """Models whose score is within ``margin`` (relative) of the top positive score."""
    top = max(scores.values(), default=0.0)
    if top <= 0:
        return []
    return [m for m, s in scores.items() if s > 0 and (top - s) / top <= margin]


def _llm_tiebreak(
    candidates: list[str], *, pages: list[PageView], topic: str | None, category: str | None, llm: LLMClient
) -> str | None:
    """Ask the model to pick one of the tied labels. Returns a valid candidate or None."""
    sample = [p.path for p in pages][:20]
    system = "You classify a website's business model. Reply with JSON only."
    prompt = (
        f"Industry / topic: {topic or category or 'unknown'}\n"
        f"Sample page paths: {', '.join(sample) or '(none)'}\n"
        f"Choose the single best business model from EXACTLY this list: {candidates}.\n"
        'Reply as {"model": "<one of the list>"}.'
    )
    try:
        data = llm.generate_json(prompt, system)
    except Exception:  # never let the tiebreak break detection
        return None
    if not isinstance(data, dict):
        return None
    choice = str(data.get("model", "")).strip().lower()
    return choice if choice in candidates else None


def detect_business_model(
    pages: list[PageView],
    *,
    site_class: SiteClass,
    topic: str | None = None,
    category: str | None = None,
    cfg: IntelligenceCfg | None = None,
    llm: LLMClient | None = None,
) -> BusinessIntent:
    """Classify the site's business model. Deterministic; optional LLM tiebreak."""
    cfg = cfg or load_intelligence_cfg()
    scores, evidence = _score_models(pages, site_class=site_class, cfg=cfg)
    _apply_industry_hints(scores, evidence, topic=topic, category=category, cfg=cfg)

    top_score = max(scores.values(), default=0.0)
    if top_score <= 0:
        return BusinessIntent(
            model=BusinessModel.LEAD_GEN,
            confidence=0.0,
            evidence=["no model signals fired — default lead_gen"],
            scores=scores,
            decided_by="default",
        )

    # Deterministic winner: highest score, ties broken by enum order for stability.
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    winner = ranked[0][0]
    decided_by = "deterministic"

    candidates = _tied_candidates(scores, cfg.llm_tiebreak_margin)
    if len(candidates) > 1 and llm is not None and getattr(llm, "enabled", False):
        choice = _llm_tiebreak(candidates, pages=pages, topic=topic, category=category, llm=llm)
        if choice is not None:
            winner = choice
            decided_by = "llm-tiebreak"

    total_score = sum(scores.values())
    confidence = round(scores[winner] / total_score, 3) if total_score else 0.0
    ev = list(evidence.get(winner, []))
    if decided_by == "llm-tiebreak":
        ev.append("llm-tiebreak")
    return BusinessIntent(
        model=BusinessModel(winner),
        confidence=confidence,
        evidence=ev,
        scores=scores,
        decided_by=decided_by,
    )
