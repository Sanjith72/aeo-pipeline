"""
Site Discovery tests — pure sitemap/robots parsing plus the full sitemap and
recursive strategies driven by an injected fake fetcher. No network, no browser:
coroutines are run with ``asyncio.run`` and ``fetch`` returns canned bodies keyed
by URL, so the discovery logic is exercised end-to-end offline.
"""

from __future__ import annotations

import asyncio

from aeo.crawl.discovery import (
    DiscoveredUrl,
    discover,
    gather_sitemap_urls,
    parse_robots_sitemaps,
    parse_sitemap,
    recursive_discover,
    seed_url,
    select_same_site,
)


def fake_fetch(pages: dict[str, str]):
    """Build an async fetcher returning canned bodies; unknown URLs → None.
    Keys may be given un-normalized — they're normalized to match lookups."""
    from aeo.utils.url import normalize

    table = {normalize(k): v for k, v in pages.items()}

    async def _fetch(url: str) -> str | None:
        return table.get(normalize(url))

    return _fetch


class TestPureParsers:
    def test_parse_robots_sitemaps(self):
        robots = (
            "User-agent: *\n"
            "Disallow: /private\n"
            "Sitemap: https://x.com/sitemap.xml\n"
            "sitemap:   https://x.com/news-sitemap.xml\n"
        )
        assert parse_robots_sitemaps(robots) == [
            "https://x.com/sitemap.xml",
            "https://x.com/news-sitemap.xml",
        ]

    def test_parse_robots_no_sitemap(self):
        assert parse_robots_sitemaps("User-agent: *\nDisallow:\n") == []

    def test_parse_sitemap_urlset(self):
        xml = """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://x.com/a</loc></url>
          <url><loc>https://x.com/b</loc></url>
        </urlset>"""
        doc = parse_sitemap(xml)
        assert doc.is_index is False
        assert doc.locs == ["https://x.com/a", "https://x.com/b"]

    def test_parse_sitemap_index(self):
        xml = """<?xml version="1.0"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <sitemap><loc>https://x.com/sm1.xml</loc></sitemap>
          <sitemap><loc>https://x.com/sm2.xml</loc></sitemap>
        </sitemapindex>"""
        doc = parse_sitemap(xml)
        assert doc.is_index is True
        assert doc.locs == ["https://x.com/sm1.xml", "https://x.com/sm2.xml"]

    def test_parse_sitemap_malformed_is_empty(self):
        doc = parse_sitemap("<not-xml")
        assert doc.is_index is False and doc.locs == []

    def test_select_same_site_filters_and_dedupes(self):
        urls = [
            "https://x.com/a",
            "https://other.com/b",   # off-site → dropped
            "https://x.com/a/",      # dupe after normalization
            "https://x.com/c",
        ]
        assert select_same_site(urls, "https://x.com/") == [
            "https://x.com/a",
            "https://x.com/c",
        ]

    def test_seed_url_accepts_bare_domain_and_full_url(self):
        assert seed_url("x.com") == "https://x.com/"
        assert seed_url("https://x.com/blog/") == "https://x.com/blog"


class TestSitemapStrategy:
    def test_walks_index_to_children_and_collects_pages(self):
        fetch = fake_fetch({
            "https://x.com/robots.txt": "Sitemap: https://x.com/sitemap_index.xml\n",
            "https://x.com/sitemap_index.xml": (
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<sitemap><loc>https://x.com/sm-blog.xml</loc></sitemap>"
                "<sitemap><loc>https://x.com/sm-product.xml</loc></sitemap>"
                "</sitemapindex>"
            ),
            "https://x.com/sm-blog.xml": (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://x.com/blog/a</loc></url>"
                "<url><loc>https://x.com/blog/b</loc></url>"
                "</urlset>"
            ),
            "https://x.com/sm-product.xml": (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://x.com/products/p1</loc></url>"
                "</urlset>"
            ),
        })
        urls = asyncio.run(gather_sitemap_urls("x.com", fetch=fetch))
        assert set(urls) == {
            "https://x.com/blog/a",
            "https://x.com/blog/b",
            "https://x.com/products/p1",
        }

    def test_falls_back_to_conventional_sitemap_when_robots_silent(self):
        fetch = fake_fetch({
            "https://x.com/robots.txt": "User-agent: *\nDisallow:\n",  # no Sitemap:
            "https://x.com/sitemap.xml": (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://x.com/only</loc></url>"
                "</urlset>"
            ),
        })
        assert asyncio.run(gather_sitemap_urls("x.com", fetch=fetch)) == ["https://x.com/only"]

    def test_respects_max_urls(self):
        many = "".join(f"<url><loc>https://x.com/p{i}</loc></url>" for i in range(10))
        fetch = fake_fetch({
            "https://x.com/robots.txt": "Sitemap: https://x.com/sitemap.xml\n",
            "https://x.com/sitemap.xml": (
                f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{many}</urlset>'
            ),
        })
        urls = asyncio.run(gather_sitemap_urls("x.com", fetch=fetch, max_urls=3))
        assert len(urls) == 3

    def test_no_sitemap_returns_empty(self):
        fetch = fake_fetch({"https://x.com/robots.txt": "User-agent: *\n"})  # sitemap.xml → None
        assert asyncio.run(gather_sitemap_urls("x.com", fetch=fetch)) == []


class TestRecursiveStrategy:
    def test_bfs_counts_inbound_links(self):
        home = (
            '<html><body>'
            '<a href="/blog/a">A</a><a href="/blog/b">B</a>'
            '<a href="https://off.com/x">off</a>'  # off-site → ignored
            "</body></html>"
        )
        page_a = '<html><body><a href="/blog/b">to B</a></body></html>'
        page_b = "<html><body>leaf</body></html>"
        fetch = fake_fetch({
            "https://x.com/": home,
            "https://x.com/blog/a": page_a,
            "https://x.com/blog/b": page_b,
        })
        graph = asyncio.run(recursive_discover("x.com", fetch=fetch, max_depth=2))
        # /blog/b is linked from both home and /blog/a → inbound 2.
        assert graph["https://x.com/blog/b"] == 2
        assert graph["https://x.com/blog/a"] == 1
        assert "https://off.com/x" not in graph

    def test_depth_limit_stops_expansion(self):
        fetch = fake_fetch({
            "https://x.com/": '<html><body><a href="/one">1</a></body></html>',
            "https://x.com/one": '<html><body><a href="/two">2</a></body></html>',
            "https://x.com/two": '<html><body><a href="/three">3</a></body></html>',
            "https://x.com/three": "<html></html>",
        })
        # max_depth=1: home(0) and /one(1) are fetched; /one's link to /two is
        # recorded as a target but /two sits at depth 2 so it is never fetched —
        # so /two's own link to /three is never seen.
        graph = asyncio.run(recursive_discover("x.com", fetch=fetch, max_depth=1))
        assert "https://x.com/two" in graph        # seen as a link target
        assert "https://x.com/three" not in graph  # never fetched → never discovered


class TestRecursiveBounds:
    """Recursive discovery walks an untrusted domain, so the in-memory working set
    must be bounded independently of the fetch budget (pathological/adversarial fan-out)."""

    def test_inbound_map_is_bounded(self, monkeypatch):
        from aeo.crawl import discovery

        monkeypatch.setattr(discovery, "_INBOUND_CAP_FACTOR", 1)  # inbound_cap = max_urls
        links = "".join(f'<a href="/p{i:02d}">{i}</a>' for i in range(20))
        fetch = fake_fetch({"https://x.com/": f"<html><body>{links}</body></html>"})
        graph = asyncio.run(discovery.recursive_discover("x.com", fetch=fetch, max_urls=10, max_depth=2))
        # seed + the first 9 novel URLs fill the cap of 10; the rest are not tracked.
        assert "https://x.com/p00" in graph
        assert "https://x.com/p08" in graph
        assert "https://x.com/p09" not in graph
        assert "https://x.com/p19" not in graph
        assert len(graph) <= 10

    def test_per_page_link_fanout_is_capped(self, monkeypatch):
        from aeo.crawl import discovery

        monkeypatch.setattr(discovery, "_MAX_LINKS_PER_PAGE", 3)
        links = "".join(f'<a href="/p{i:02d}">{i}</a>' for i in range(10))
        fetch = fake_fetch({"https://x.com/": f"<html><body>{links}</body></html>"})
        graph = asyncio.run(discovery.recursive_discover("x.com", fetch=fetch, max_urls=50, max_depth=2))
        # only the first 3 links on the homepage are processed.
        assert "https://x.com/p00" in graph
        assert "https://x.com/p02" in graph
        assert "https://x.com/p03" not in graph


class TestDiscover:
    def test_prefers_sitemap(self):
        fetch = fake_fetch({
            "https://x.com/robots.txt": "Sitemap: https://x.com/sitemap.xml\n",
            "https://x.com/sitemap.xml": (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                "<url><loc>https://x.com/a</loc></url></urlset>"
            ),
        })
        result = asyncio.run(discover("x.com", fetch=fetch))
        assert result.source == "sitemap"
        assert result.urls == [DiscoveredUrl("https://x.com/a", 0)]

    def test_falls_back_to_recursive(self):
        fetch = fake_fetch({
            # no robots, no sitemap.xml → sitemap strategy is empty
            "https://x.com/": '<html><body><a href="/a">A</a></body></html>',
            "https://x.com/a": "<html></html>",
        })
        result = asyncio.run(discover("x.com", fetch=fetch))
        assert result.source == "recursive"
        assert {u.url for u in result.urls} == {"https://x.com/", "https://x.com/a"}

    def test_seed_only_when_nothing_discoverable(self):
        fetch = fake_fetch({})  # everything → None, even the homepage
        result = asyncio.run(discover("x.com", fetch=fetch))
        assert result.source == "seed"
        assert result.urls == [DiscoveredUrl("https://x.com/", 0)]
