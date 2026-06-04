"""
Integration: Site Discovery → Page Prioritization over the *real* shipped config.

Unit tests inject hand-built prioritization configs; this one wires the actual
discovery parser to the shipped ``config/prioritization.yaml`` to prove the two
blocks compose — a realistic cybersecurity sitemap flows through ``discover`` into
``prioritize`` and comes out classified and ranked the way the config intends.
Fully offline: the fetcher returns canned robots/sitemap bodies.
"""

from __future__ import annotations

import asyncio

from aeo.crawl.discovery import discover
from aeo.crawl.prioritize import PageInput, load_prioritization_cfg, prioritize

SITEMAP = (
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://securin.io/</loc></url>"
    "<url><loc>https://securin.io/products/vmdr</loc></url>"
    "<url><loc>https://securin.io/solutions/attack-surface</loc></url>"
    "<url><loc>https://securin.io/resources/what-is-cpsm</loc></url>"
    "<url><loc>https://securin.io/blog/cve-2024-1234</loc></url>"
    "<url><loc>https://securin.io/about-us</loc></url>"
    "<url><loc>https://securin.io/login</loc></url>"
    "<url><loc>https://other-domain.com/off-site</loc></url>"  # must be filtered
    "</urlset>"
)


def _fake_fetch(url: str):
    table = {
        "https://securin.io/robots.txt": "Sitemap: https://securin.io/sitemap.xml\n",
        "https://securin.io/sitemap.xml": SITEMAP,
    }

    async def _fetch(u: str):
        return table.get(u)

    return _fetch


def test_real_config_classifies_and_ranks_discovered_urls():
    result = asyncio.run(discover("securin.io", fetch=_fake_fetch("")))

    # Discovery: sitemap source, off-site URL filtered out.
    assert result.source == "sitemap"
    urls = {u.url for u in result.urls}
    assert "https://other-domain.com/off-site" not in urls
    assert len(result.urls) == 7

    # Prioritization over the shipped weights. With no link graph every
    # traffic_signal floors to 1.0, so URLs rank purely by page-type base_weight.
    cfg = load_prioritization_cfg()
    scored = prioritize([PageInput(u.url, u.internal_links) for u in result.urls], cfg)

    assert [s.page_type for s in scored] == [
        "pillar",    # 1.0  resources/what-is-cpsm
        "product",   # 0.9  products/vmdr   (ties broken alphabetically: products < solutions)
        "solution",  # 0.9  solutions/attack-surface
        "blog",      # 0.8  blog/cve-2024-1234
        "homepage",  # 0.7  /
        "about",     # 0.4  about-us
        "utility",   # 0.2  login
    ]
    # 7 URLs < top_n (30) → all selected; ranks are 1..7 in order.
    assert all(s.selected for s in scored)
    assert [s.rank for s in scored] == [1, 2, 3, 4, 5, 6, 7]
