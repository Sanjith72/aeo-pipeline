"""
Site Discovery — turn a domain into the URL inventory the prioritizer ranks.

Two strategies, tried in order:

  1. **Sitemap** — read ``robots.txt`` for ``Sitemap:`` directives (and try the
     conventional ``/sitemap.xml`` as a fallback), then walk sitemap *indexes* to
     their child sitemaps and collect every same-site ``<loc>``. Cheap and
     authoritative — most real sites publish one.
  2. **Recursive** — when no sitemap is found, breadth-first crawl from the
     homepage following same-site ``<a href>`` links up to a page/depth budget,
     recording the internal-link graph so each URL carries an inbound-link count.

Discovery uses plain HTTP GETs (httpx), **not** the rendering crawler: harvesting
links and sitemaps needs no JavaScript. The expensive Crawl4AI render is reserved
for the per-page pipeline that runs on the *selected* top-N.

The inbound-link count becomes the prioritizer's ``traffic_signal``. Sitemap URLs
have no graph, so they rank on ``base_weight`` alone (floored to
``min_traffic_signal``); recursively discovered URLs carry a real count. Every
network call goes through an injectable ``fetch`` callable, so the discovery logic
is unit-tested with no network at all.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import httpx

from ..logging import get_logger
from ..settings import get_settings
from ..utils.url import absolute, normalize, same_site

log = get_logger(__name__)

# A fetcher returns the response body (text) or None on any failure / non-200.
FetchText = Callable[[str], Awaitable["str | None"]]

# Recursive discovery walks an *external, untrusted* domain, so the in-memory
# working set must be bounded independently of the fetch budget — a single
# pathological/adversarial page can emit millions of internal links. These caps are
# generous multiples of the fetch budget (no effect on real sites) but keep the
# frontier/inbound graph from growing without limit before the budget stops the BFS.
_FRONTIER_CAP_FACTOR = 4   # frontier length ≤ max_urls * this
_INBOUND_CAP_FACTOR = 10   # distinct inbound URLs tracked ≤ max_urls * this
_MAX_LINKS_PER_PAGE = 500  # links processed from any single page


@dataclass(slots=True)
class DiscoveredUrl:
    url: str
    internal_links: int = 0


@dataclass(slots=True)
class DiscoveryResult:
    domain: str
    source: str  # "sitemap" | "recursive" | "seed"
    urls: list[DiscoveredUrl] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.urls)


@dataclass(slots=True)
class SitemapDoc:
    is_index: bool
    locs: list[str]


# ---------------------------------------------------------------------------
# pure parsers (no network)
# ---------------------------------------------------------------------------


def parse_robots_sitemaps(text: str) -> list[str]:
    """Extract ``Sitemap:`` directive URLs from robots.txt (key is case-insensitive)."""
    out: list[str] = []
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "sitemap":
            url = value.strip()
            if url:
                out.append(url)
    return out


def _localname(tag: str) -> str:
    """Strip an XML namespace: ``{http://…}loc`` → ``loc``."""
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(text: str) -> SitemapDoc:
    """Parse a sitemap or sitemap index, tolerant of namespaces. Returns the child
    sitemap locs (index) or the page locs (urlset). Malformed XML → empty."""
    try:
        root = ET.fromstring(text.strip())
    except ET.ParseError:
        return SitemapDoc(is_index=False, locs=[])
    is_index = _localname(root.tag) == "sitemapindex"
    locs = [
        el.text.strip()
        for el in root.iter()
        if _localname(el.tag) == "loc" and el.text and el.text.strip()
    ]
    return SitemapDoc(is_index=is_index, locs=locs)


def select_same_site(urls: list[str], domain: str) -> list[str]:
    """Normalize, keep only same-site URLs, dedupe preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if not u:
            continue
        norm = normalize(u)
        if not same_site(norm, domain) or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def seed_url(domain: str) -> str:
    """A bare domain becomes ``https://domain/``; a full URL passes through
    normalized so callers can hand us either."""
    d = (domain or "").strip()
    if "://" not in d:
        d = "https://" + d
    return normalize(d)


# ---------------------------------------------------------------------------
# network (injectable fetcher)
# ---------------------------------------------------------------------------


async def _default_fetch_text(url: str) -> str | None:
    cfg = get_settings().crawler
    from .transport import async_transport

    try:
        async with httpx.AsyncClient(
            timeout=cfg.discovery.timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": cfg.user_agent},
            transport=async_transport(),  # force IPv4 on OCI Ampere when configured
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            log.info("discovery_fetch_non200", url=url, status=resp.status_code)
    except Exception as exc:
        log.warning("discovery_fetch_failed", url=url, error=str(exc))
    return None


async def gather_sitemap_urls(
    domain: str,
    *,
    fetch: FetchText | None = None,
    max_urls: int | None = None,
    max_docs: int | None = None,
) -> list[str]:
    """Collect same-site page URLs from the domain's sitemap(s). Walks sitemap
    indexes to their children. Empty when no sitemap exists or it yields nothing."""
    fetch = fetch or _default_fetch_text
    cfg = get_settings().crawler.discovery
    max_urls = max_urls or cfg.max_urls
    max_docs = max_docs or cfg.max_sitemaps
    seed = seed_url(domain)

    robots = await fetch(absolute(seed, "/robots.txt"))
    queue = parse_robots_sitemaps(robots) if robots else []
    if not queue:
        queue = [absolute(seed, "/sitemap.xml")]

    pages: list[str] = []
    seen_docs: set[str] = set()
    fetched = 0
    while queue and fetched < max_docs and len(pages) < max_urls:
        sm_url = normalize(queue.pop(0))
        if sm_url in seen_docs:
            continue
        seen_docs.add(sm_url)
        text = await fetch(sm_url)
        fetched += 1
        if not text:
            continue
        doc = parse_sitemap(text)
        if doc.is_index:
            queue.extend(select_same_site(doc.locs, seed))  # child sitemaps
        else:
            pages.extend(doc.locs)

    return select_same_site(pages, seed)[:max_urls]


async def recursive_discover(
    domain: str,
    *,
    fetch: FetchText | None = None,
    max_urls: int | None = None,
    max_depth: int | None = None,
) -> dict[str, int]:
    """BFS from the homepage following same-site links. Returns ``{url: inbound}``
    over the discovered set, capped to ``max_urls`` (highest inbound first)."""
    fetch = fetch or _default_fetch_text
    cfg = get_settings().crawler.discovery
    max_urls = max_urls or cfg.max_urls
    max_depth = max_depth if max_depth is not None else cfg.max_depth
    seed = seed_url(domain)

    # Local imports: keep bs4 off the module-load path (mirrors crawl/client.py).
    from ..extract.links import extract as extract_links
    from ..utils.html import parse

    inbound: dict[str, int] = {seed: 0}
    visited: set[str] = set()
    frontier: list[tuple[str, int]] = [(seed, 0)]
    fetched_any = False
    inbound_cap = max_urls * _INBOUND_CAP_FACTOR
    frontier_cap = max_urls * _FRONTIER_CAP_FACTOR

    while frontier and len(visited) < max_urls:
        url, depth = frontier.pop(0)
        if url in visited:
            continue
        visited.add(url)
        html = await fetch(url)
        if not html:
            continue
        fetched_any = True
        soup = parse(html)
        for raw in extract_links(html, soup, url).get("internal", [])[:_MAX_LINKS_PER_PAGE]:
            norm = normalize(raw)
            if not same_site(norm, seed):
                continue
            if norm in inbound:
                inbound[norm] += 1
            elif len(inbound) < inbound_cap:
                inbound[norm] = 1
            else:
                continue  # working set saturated → stop tracking novel URLs
            if norm not in visited and depth < max_depth and len(frontier) < frontier_cap:
                frontier.append((norm, depth + 1))

    # Nothing fetched at all (homepage unreachable) → discovered nothing real;
    # let the caller fall through to the explicit homepage-seed result.
    if not fetched_any:
        return {}

    ranked = sorted(inbound.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(ranked[:max_urls])


async def discover(
    domain: str,
    *,
    fetch: FetchText | None = None,
    max_urls: int | None = None,
    prefer_recursive: bool = False,
) -> DiscoveryResult:
    """Discover a site's URL inventory: sitemap first, recursive fallback, and a
    last-resort seed of the homepage so the pipeline always has something to run."""
    seed = seed_url(domain)
    cfg = get_settings().crawler.discovery
    max_urls = max_urls or cfg.max_urls

    if not prefer_recursive:
        sitemap = await gather_sitemap_urls(domain, fetch=fetch, max_urls=max_urls)
        if sitemap:
            log.info("discovery_sitemap", domain=seed, urls=len(sitemap))
            return DiscoveryResult(
                domain=seed, source="sitemap",
                urls=[DiscoveredUrl(u) for u in sitemap],
            )

    graph = await recursive_discover(domain, fetch=fetch, max_urls=max_urls)
    if graph:
        log.info("discovery_recursive", domain=seed, urls=len(graph))
        return DiscoveryResult(
            domain=seed, source="recursive",
            urls=[DiscoveredUrl(u, n) for u, n in graph.items()],
        )

    log.info("discovery_seed_only", domain=seed)
    return DiscoveryResult(domain=seed, source="seed", urls=[DiscoveredUrl(seed)])
