"""
Pack construction (v5 CH-03; contract: docs/V5_CONTRACTS.md §b).

Groups the prioritized page ranking into bounded, impact-ordered packs:

  * **Pack 1 is always the homepage pack** — an explicit rule, not emergent from the
    ranking (the homepage's base_weight 0.7 sits below pillar/product, so it would
    otherwise drift out of the top pack). It carries the homepage plus the
    highest-value remaining pages: the "first impression" set.
  * Later packs group the rest by page-type family (products/solutions/pricing,
    trust, content) so each pack is a coherent chunk of work, then order by summed
    ``final_score`` — never crawl or alphabetical order.
  * No pack exceeds :data:`MAX_PACK_PAGES`.

Pure functions over the prioritization output (``crawl.prioritize.ScoredUrl`` or the
equivalent dicts from ``Orchestrator.dry_run``'s ``ranking``); persistence (the
``packs`` table, migration 0028) starts in P3 — the P1 free overview returns packs
unpersisted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# §9.1 resolved 2026-07-23: the spec's cap ("no pack exceeds 5 pages") won over the
# transcript's 6. Changing the cap is this one line.
MAX_PACK_PAGES = 5

# Page-type families for the post-homepage packs, in presentation order. Types not
# listed fall into the catch-all.
_FAMILIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("product", "Products & solutions", ("product", "solution", "pricing")),
    ("trust", "Trust & about", ("about", "contact")),
    ("content", "Content & authority", ("pillar", "blog")),
)
_OTHER_FAMILY = ("other", "Supporting pages")


@dataclass(slots=True)
class PackPage:
    url: str
    page_type: str
    final_score: float
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "page_type": self.page_type,
            "final_score": self.final_score,
            "rank": self.rank,
        }


@dataclass(slots=True)
class Pack:
    pack_index: int
    title: str
    pages: list[PackPage] = field(default_factory=list)

    @property
    def impact_score(self) -> float:
        return round(sum(p.final_score for p in self.pages), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_index": self.pack_index,
            "title": self.title,
            "impact_score": self.impact_score,
            "page_count": len(self.pages),
            "pages": [p.to_dict() for p in self.pages],
        }


def _as_page(item: Any) -> PackPage:
    """Accept a ScoredUrl or the ranking dicts dry_run emits."""
    if isinstance(item, Mapping):
        return PackPage(
            url=str(item.get("url", "")),
            page_type=str(item.get("page_type", "default")),
            final_score=float(item.get("final_score", 0.0)),
            rank=int(item.get("rank", 0)),
        )
    return PackPage(
        url=item.url,
        page_type=item.page_type,
        final_score=float(item.final_score),
        rank=int(item.rank),
    )


def _family_for(page_type: str) -> tuple[str, str]:
    for key, title, types in _FAMILIES:
        if page_type in types:
            return key, title
    return _OTHER_FAMILY


def _chunk(pages: list[PackPage], size: int) -> list[list[PackPage]]:
    return [pages[i : i + size] for i in range(0, len(pages), size)]


def build_packs(pages: Iterable[Any], *, max_pages: int = MAX_PACK_PAGES) -> list[Pack]:
    """Group a prioritized ranking into ordered packs.

    Only ``selected`` pages join packs when the selected flag is available (dicts from
    ``dry_run`` carry it; a caller passing a pre-filtered list simply omits it) — packs
    are the work queue, and work happens on the crawl-worthy set.
    """
    candidates: list[PackPage] = []
    for item in pages:
        page = _as_page(item)
        if not page.url:
            continue
        selected = (
            item.get("selected", True) if isinstance(item, Mapping) else getattr(item, "selected", True)
        )
        # The homepage is ALWAYS admitted — "homepage in Pack 1" is an explicit rule, not
        # emergent from the ranking. On a large content site its base_weight (0.7) drops it
        # below the top-N selected cut, so filtering on `selected` first would silently
        # exclude the very page the rule exists to guarantee.
        if not selected and page.page_type != "homepage":
            continue
        candidates.append(page)
    if not candidates:
        return []

    candidates.sort(key=lambda p: (-p.final_score, p.rank, p.url))

    # Pack 1: the homepage (rule) + the highest-value remaining pages.
    homepages = [p for p in candidates if p.page_type == "homepage"]
    home = homepages[0] if homepages else None
    rest = [p for p in candidates if p is not home]
    pack1_pages = ([home] if home else []) + rest[: max_pages - (1 if home else 0)]
    packs = [Pack(pack_index=1, title="Homepage & first impression", pages=pack1_pages)]

    # Later packs: family-grouped chunks of the remainder, ordered by impact.
    remainder = rest[max_pages - (1 if home else 0) :]
    buckets: dict[str, tuple[str, list[PackPage]]] = {}
    for page in remainder:
        key, title = _family_for(page.page_type)
        buckets.setdefault(key, (title, []))[1].append(page)

    later: list[Pack] = []
    for _key, (title, bucket) in buckets.items():
        for i, chunk in enumerate(_chunk(bucket, max_pages)):
            chunk_title = title if i == 0 else f"{title} · part {i + 1}"
            later.append(Pack(pack_index=0, title=chunk_title, pages=chunk))
    later.sort(key=lambda p: -p.impact_score)

    for i, pack in enumerate(later, start=2):
        pack.pack_index = i
    packs.extend(later)
    return packs
