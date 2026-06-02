"""Crawler with content-hash change gate — no LLM, pure HTTP + BeautifulSoup."""
from __future__ import annotations
import asyncio
import json as _json
from collections import deque
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
import xxhash
from bs4 import BeautifulSoup

from aeo.config import get_settings
from aeo.models.blueprint import CrawledPage
from aeo.utils.observability import get_logger, get_tracer

logger = get_logger(__name__)
tracer = get_tracer(__name__)


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


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    known_hashes: dict[str, str],
) -> CrawledPage:
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body_text = soup.get_text(separator=" ", strip=True)
    content_hash = xxhash.xxh64(body_text.encode()).hexdigest()
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
) -> list[CrawledPage]:
    settings = get_settings()
    known_hashes = known_hashes or {}
    base_domain = urlparse(start_url).netloc.removeprefix("www.")

    visited: set[str] = set()
    queue: deque[str] = deque([start_url])
    pages: list[CrawledPage] = []
    sem = asyncio.Semaphore(settings.crawler_max_concurrent)

    with tracer.start_as_current_span("crawler.crawl_site") as span:
        span.set_attribute("start_url", start_url)
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.crawler_user_agent},
            timeout=float(settings.crawler_timeout_seconds),
        ) as client:
            while queue and len(visited) < settings.crawler_max_pages:
                url = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)

                async with sem:
                    try:
                        page = await _fetch(client, url, known_hashes)
                        pages.append(page)
                        logger.debug("crawled", url=url, changed=page.changed)
                        budget = settings.crawler_max_pages - len(visited) - len(queue)
                        for link in page.links:
                            if link not in visited and _same_domain(link, base_domain) and budget > 0:
                                queue.append(link)
                                budget -= 1
                    except Exception as exc:
                        logger.warning("crawl_error", url=url, error=str(exc))

        logger.info("crawl_done", domain=base_domain, total=len(pages))
        span.set_attribute("pages_crawled", len(pages))
        return pages
