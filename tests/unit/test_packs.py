"""v5 CH-03 — pack construction (bounded, impact-ordered, homepage always Pack 1)."""

from __future__ import annotations

from aeo.crawl.prioritize import PageInput, prioritize
from aeo.pipeline.packs import MAX_PACK_PAGES, build_packs


def _ranking(pages: list[PageInput]) -> list[dict]:
    return [
        {"url": s.url, "page_type": s.page_type, "final_score": s.final_score,
         "rank": s.rank, "selected": s.selected}
        for s in prioritize(pages)
    ]


def test_homepage_always_in_pack_1_even_when_unselected() -> None:
    # A 51-URL sitemap site (internal_links=0 → final_score == base_weight): the homepage's
    # 0.7 weight ranks it below 30+ content pages, so it is NOT `selected`. The rule must
    # still put it in Pack 1 (the regression the review caught).
    pages = (
        [PageInput("https://x.com/")]
        + [PageInput(f"https://x.com/blog/p{i}") for i in range(40)]
        + [PageInput(f"https://x.com/products/s{i}") for i in range(10)]
    )
    ranking = _ranking(pages)
    home = next(r for r in ranking if r["page_type"] == "homepage")
    assert home["selected"] is False  # precondition: homepage fell out of the top-N

    packs = build_packs(ranking)
    assert packs[0].pack_index == 1
    assert any(p.page_type == "homepage" for p in packs[0].pages), "homepage must be in Pack 1"


def test_no_pack_exceeds_the_cap() -> None:
    pages = [PageInput("https://x.com/")] + [PageInput(f"https://x.com/p{i}") for i in range(30)]
    for pack in build_packs(_ranking(pages)):
        assert len(pack.pages) <= MAX_PACK_PAGES


def test_packs_impact_ordered_after_pack_1() -> None:
    pages = [PageInput("https://x.com/")] + [PageInput(f"https://x.com/p{i}") for i in range(20)]
    packs = build_packs(_ranking(pages))
    later = [p.impact_score for p in packs[1:]]
    assert later == sorted(later, reverse=True)


def test_empty_ranking_yields_no_packs() -> None:
    assert build_packs([]) == []


def test_site_without_homepage_still_packs() -> None:
    # A ranking with no homepage entry must not crash — Pack 1 just leads with top value.
    pages = [PageInput(f"https://x.com/p{i}") for i in range(8)]
    packs = build_packs(_ranking(pages))
    assert packs and packs[0].pack_index == 1
