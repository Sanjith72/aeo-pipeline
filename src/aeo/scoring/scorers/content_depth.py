"""
Criterion 6 — Content Depth.

The one hybrid scorer. A deterministic base is computed from word count,
methodology language, embedded stats, and promotional density; when the LLM
is enabled it returns an independent 1-5 judgement and the two are averaged.
Deterministic-only when Ollama is absent — never blocks a run.

Operates on the chunker's reconstructed text so it never re-parses HTML.
"""

from __future__ import annotations

from typing import Any

from ...nlp import load_prompt, tone
from ...nlp.llm import LLMClient
from ...storage.models import CriterionScore, ExtractionBundle
from ..result import ScoreContext, clamp_tier

_LLM_CONTENT_CAP = 4000
_TONE_CONTENT_CAP = 20000


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("content_depth")
    min_wc = int(crit.cfg.get("min_word_count_for_credit", 400))
    method_kw = [k.lower() for k in crit.cfg.get("methodology_keywords", [])]

    read = ctx.bundle.get("readability", {}) or {}
    stats = ctx.bundle.get("stats", {}) or {}
    headings = ctx.bundle.get("headings", {}) or {}

    wc = int(read.get("word_count", 0) or 0)
    text = _chunk_text(ctx.bundle)
    method_hits = _distinct_keyword_hits(text, method_kw)
    stats_count = int(stats.get("count", 0) or 0)
    tone_info = tone.analyze(text[:_TONE_CONTENT_CAP])
    promotional = bool(tone_info["is_promotional"])

    # Deterministic base from length.
    if wc < 200:
        base = 1
    elif wc < min_wc:
        base = 2
    elif wc < 800:
        base = 3
    elif wc < 1500:
        base = 4
    else:
        base = 5

    det = base
    if method_hits >= 2 or stats_count >= 6:
        det = clamp_tier(det + 1)
    if promotional and wc < 800:
        det = clamp_tier(det - 1)

    value = det
    scored_by = "deterministic"
    judgement: dict[str, Any] | None = None

    if ctx.llm is not None and ctx.llm.enabled and wc >= 50 and text:
        judgement = _llm_depth(ctx.llm, ctx.bundle, text, wc)
        score_val = judgement.get("depth_score") if judgement else None
        if isinstance(score_val, (int, float)):
            llm_tier = clamp_tier(score_val)
            value = clamp_tier((det + llm_tier) / 2)
            scored_by = "hybrid"

    return CriterionScore(
        name="content_depth",
        value=value,
        evidence={
            "word_count": wc,
            "deterministic_base": det,
            "methodology_keyword_hits": method_hits,
            "stats_count": stats_count,
            "h23_total": headings.get("h23_total", 0),
            "promotional": promotional,
            "promotional_phrases": tone_info["marketing_phrases"][:10],
            "llm_judgement": judgement,
        },
        notes=_notes(wc, min_wc, promotional, scored_by),
        scored_by=scored_by,
    )


def _chunk_text(bundle: ExtractionBundle) -> str:
    chunker = bundle.get("chunker", {}) or {}
    chunks = chunker.get("chunks", []) or []
    return " ".join(c.get("text", "") for c in chunks).strip()


def _distinct_keyword_hits(text: str, keywords: list[str]) -> int:
    low = text.lower()
    return sum(1 for kw in keywords if kw and kw in low)


def _llm_depth(llm: LLMClient, bundle: ExtractionBundle, text: str, wc: int) -> dict[str, Any] | None:
    meta = bundle.get("meta", {}) or {}
    title = (meta.get("title") or "")[:200]
    prompt = (
        load_prompt("content_depth")
        .replace("<<TITLE>>", title)
        .replace("<<WORD_COUNT>>", str(wc))
        .replace("<<CONTENT>>", text[:_LLM_CONTENT_CAP])
    )
    return llm.generate_json(prompt)


def _notes(wc: int, min_wc: int, promotional: bool, scored_by: str) -> str:
    bits = [f"{wc} words"]
    if wc < min_wc:
        bits.append(f"below {min_wc}-word credit threshold")
    if promotional:
        bits.append("promotional tone")
    if scored_by == "hybrid":
        bits.append("LLM-assisted")
    return "; ".join(bits)
