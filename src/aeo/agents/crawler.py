"""Crawler with SHA-256 content-hash change gate — no LLM, pure HTTP + BeautifulSoup."""
from __future__ import annotations
import asyncio
import hashlib
import json as _json
from collections import deque
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from aeo.config import get_settings
from aeo.models.blueprint import Blueprint, CrawledPage
from aeo.utils.http import async_client
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Default Top-N cap for prioritized harvesting (configurable per call).
DEFAULT_TOP_N = 50


def _content_hash(body_text: str) -> str:
    """SHA-256 (64-char hex) of normalized page content — the change-gate fingerprint.

    NOTE: prior revisions used xxhash.xxh64 here for speed. v4 mandates SHA-256 so the
    hash is cryptographic, deterministic across runtimes, and a fixed 64 chars (stored in
    the CHAR(64)-compatible content_hashes.hash column). xxhash remains a project dep but
    is no longer used by the gate.
    """
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()


def _extract_schema_markup(soup: BeautifulSoup) -> list[dict]:
    schemas: list[dict] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(tag.string or "")
            schemas.append(data)
        except Exception:
            pass
    return schemas


def _extract_headings(soup: BeautifulSoup) -> list[str]:
    return [tag.get_text(strip=True) for tag in soup.find_all(["h1", "h2", "h3", "h4"])]


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])
        if urlparse(full).scheme in ("http", "https"):
            seen.add(full)
    return list(seen)


def _same_domain(url: str, base: str) -> bool:
    netloc = urlparse(url).netloc
    return netloc == base or netloc == f"www.{base}" or netloc.removeprefix("www.") == base


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _http_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET with exponential backoff (max 3 attempts) around the network call."""
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    known_hashes: dict[str, str],
) -> CrawledPage:
    resp = await _http_get(client, url)

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body_text = soup.get_text(separator=" ", strip=True)
    # SHA-256 content-hash gate: compare freshly computed hash against the stored hash;
    # `changed=False` means callers skip the page entirely (no processing).
    content_hash = _content_hash(body_text)
    changed = known_hashes.get(url) != content_hash

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url

    return CrawledPage(
        url=url,
        content_hash=content_hash,
        title=title,
        headings=_extract_headings(soup),
        body_text=body_text[:50_000],
        links=_extract_links(soup, url),
        schema_markup=_extract_schema_markup(soup),
        crawled_at=datetime.utcnow(),
        changed=changed,
    )


async def crawl_site(
    start_url: str,
    known_hashes: dict[str, str] | None = None,
    max_pages: int | None = None,
) -> list[CrawledPage]:
    settings = get_settings()
    known_hashes = known_hashes or {}
    page_cap = max_pages if max_pages is not None else settings.crawler_max_pages
    base_domain = urlparse(start_url).netloc.removeprefix("www.")

    visited: set[str] = set()
    queue: deque[str] = deque([start_url])
    pages: list[CrawledPage] = []
    sem = asyncio.Semaphore(settings.crawler_max_concurrent)

    with tracer.start_as_current_span("crawler.crawl_site") as span:
        span.set_attribute("start_url", start_url)
        # async_client forces IPv4 egress (OCI ARM / Ampere drops IPv6 silently).
        async with async_client(
            headers={"User-Agent": settings.crawler_user_agent},
            timeout=float(settings.crawler_timeout_seconds),
        ) as client:
            while queue and len(visited) < page_cap:
                url = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)

                async with sem:
                    try:
                        page = await _fetch(client, url, known_hashes)
                        pages.append(page)
                        logger.debug("crawled", url=url, changed=page.changed)
                        budget = page_cap - len(visited) - len(queue)
                        for link in page.links:
                            if link not in visited and _same_domain(link, base_domain) and budget > 0:
                                queue.append(link)
                                budget -= 1
                    except Exception as exc:
                        logger.warning("crawl_error", url=url, error=str(exc))

        logger.info("crawl_done", domain=base_domain, total=len(pages))
        span.set_attribute("pages_crawled", len(pages))
        return pages


def _relevance_score(page: CrawledPage, blueprint: Blueprint) -> int:
    """Deterministic relevance score: how many blueprint signals appear on the page."""
    haystack = f"{page.title} {' '.join(page.headings)} {page.body_text}".lower()
    score = 0
    for entity in blueprint.required_entities:
        if entity.lower() in haystack:
            score += 2  # entity presence weighted higher than loose query keywords
    for query in blueprint.target_queries:
        keywords = [w for w in query.lower().split() if len(w) > 3]
        if keywords and sum(1 for w in keywords if w in haystack) >= len(keywords) / 2:
            score += 1
    return score


def prioritize_urls(
    pages: list[CrawledPage],
    blueprint: Blueprint,
    top_n: int = DEFAULT_TOP_N,
) -> list[str]:
    """Derive a prioritized Top-N URL list from crawled pages against a blueprint.

    Ranks by blueprint relevance (entity + query-keyword overlap), then prefers changed
    pages. Returns at most ``top_n`` URLs. Pure/deterministic — no LLM or network calls.
    """
    ranked = sorted(
        pages,
        key=lambda p: (_relevance_score(p, blueprint), p.changed),
        reverse=True,
    )
    return [p.url for p in ranked[:top_n]]
