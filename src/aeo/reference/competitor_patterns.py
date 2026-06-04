"""
Competitor structural-pattern extraction — L1 (the empirical floor).

Pure aggregation over the competitor pages the crawler already extracted: what
page-types they publish, which JSON-LD types they use, which topic entities they
cover, how long their pages run, and which question-shaped headings recur. This
keeps the generated blueprint grounded in *what Pentera/Cymulate/Picus actually
do* rather than a theoretical ideal — it's the floor the framework (L2) raises to
a ceiling and the LLM (L3) synthesizes between.

No I/O: it reads ``(url, ExtractionBundle)`` pairs the caller loads from the DB
(or builds in a test). Every section access is defensive — a competitor bundle
may be partial, and L1 must never raise into the generator.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..crawl.prioritize import PrioritizationCfg, classify, load_prioritization_cfg
from ..storage.models import ExtractionBundle

# A reasonable cap so a pathological site can't dominate the prompt/patterns.
_MAX_QUESTION_HEADINGS = 25


@dataclass(slots=True)
class CompetitorPatterns:
    """Aggregated structural signals across a competitor set."""

    domains: list[str] = field(default_factory=list)
    page_count: int = 0
    page_type_counts: dict[str, int] = field(default_factory=dict)
    schema_type_counts: dict[str, int] = field(default_factory=dict)
    entity_coverage: dict[str, int] = field(default_factory=dict)  # entity -> # pages mentioning
    avg_word_count_by_type: dict[str, float] = field(default_factory=dict)
    common_question_headings: list[str] = field(default_factory=list)

    def page_type_share(self, page_type: str) -> float:
        """Fraction of competitor pages of this type — the empirical-floor weight."""
        if self.page_count <= 0:
            return 0.0
        return self.page_type_counts.get(page_type, 0) / self.page_count

    def covered_entities(self, *, min_pages: int = 1) -> list[str]:
        """Entities covered by at least ``min_pages`` competitor pages, most-covered first."""
        return [e for e, n in sorted(self.entity_coverage.items(), key=lambda kv: (-kv[1], kv[0])) if n >= min_pages]

    def to_summary(self) -> dict:
        """Compact dict for the synthesis prompt / blueprint notes."""
        return {
            "domains": self.domains,
            "page_count": self.page_count,
            "page_type_counts": self.page_type_counts,
            "top_schema_types": dict(Counter(self.schema_type_counts).most_common(8)),
            "covered_entities": self.covered_entities(),
            "common_question_headings": self.common_question_headings[:10],
        }


def _searchable_text(bundle: ExtractionBundle) -> str:
    """Title + headings + chunk text, lowercased — enough to detect entity mentions
    without depending on a full-text section that may not be present."""
    parts: list[str] = []
    meta = bundle.get("meta", {}) or {}
    parts.append(str(meta.get("title", "")))
    headings = bundle.get("headings", {}) or {}
    by_level = headings.get("by_level", {}) or {}
    for lvl in ("h1", "h2", "h3"):
        parts.extend(str(h) for h in (by_level.get(lvl, []) or []))
    chunker = bundle.get("chunker", {}) or {}
    for ch in (chunker.get("chunks", []) or []):
        parts.append(str(ch.get("text", "")))
    return " ".join(parts).lower()


def _question_headings(bundle: ExtractionBundle) -> list[str]:
    headings = bundle.get("headings", {}) or {}
    out: list[str] = []
    for h in (headings.get("sequence", []) or []):
        if h.get("is_question") and h.get("tag") in ("h2", "h3"):
            text = str(h.get("text", "")).strip()
            if text:
                out.append(text)
    return out


def extract_patterns(
    pages: list[tuple[str, ExtractionBundle]],
    *,
    allowed_entities: list[str],
    domains: list[str] | None = None,
    cfg: PrioritizationCfg | None = None,
) -> CompetitorPatterns:
    """Aggregate structural patterns across ``(url, bundle)`` competitor pages."""
    cfg = cfg or load_prioritization_cfg()
    entity_lookup = {e.lower(): e for e in allowed_entities}

    page_types: Counter[str] = Counter()
    schema_types: Counter[str] = Counter()
    entity_pages: Counter[str] = Counter()
    words_by_type: dict[str, list[int]] = {}
    question_headings: Counter[str] = Counter()
    seen_domains: list[str] = list(domains or [])

    for url, bundle in pages:
        page_type = classify(url, cfg)
        page_types[page_type] += 1

        schema = bundle.get("schema_jsonld", {}) or {}
        for t in (schema.get("types", []) or []):
            schema_types[str(t)] += 1

        read = bundle.get("readability", {}) or {}
        wc = read.get("word_count")
        if isinstance(wc, (int, float)) and wc > 0:
            words_by_type.setdefault(page_type, []).append(int(wc))

        text = _searchable_text(bundle)
        if text:
            for low, canonical in entity_lookup.items():
                if low in text:
                    entity_pages[canonical] += 1

        for qh in _question_headings(bundle):
            question_headings[qh] += 1

    avg_words = {
        pt: round(sum(vals) / len(vals), 1) for pt, vals in words_by_type.items() if vals
    }
    common_q = [h for h, _ in question_headings.most_common(_MAX_QUESTION_HEADINGS)]

    return CompetitorPatterns(
        domains=seen_domains,
        page_count=len(pages),
        page_type_counts=dict(page_types),
        schema_type_counts=dict(schema_types),
        entity_coverage=dict(entity_pages),
        avg_word_count_by_type=avg_words,
        common_question_headings=common_q,
    )
