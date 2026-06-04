"""
Bounded-concurrency batch crawl.

Single CrawlClient is shared across all coroutines so the browser is reused.
Concurrency is gated by a semaphore; politeness adds per-host pacing on top.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from ..logging import get_logger
from ..settings import get_settings
from ..storage.models import FetchedPage
from .client import CrawlClient
from .retry import fetch_retry

log = get_logger(__name__)


async def fetch_many(urls: Iterable[str]) -> list[FetchedPage]:
    """Fan out a list of URLs, return results in input order."""
    s = get_settings()
    sem = asyncio.Semaphore(s.crawler.concurrency)
    url_list = list(urls)

    async with CrawlClient() as client:
        async def _one(idx: int, url: str) -> tuple[int, FetchedPage]:
            async with sem:
                try:
                    async for attempt in fetch_retry():
                        with attempt:
                            page = await client.fetch(url)
                    log.info("crawl_ok", url=url, http=page.http_status,
                             ms=page.fetch_duration_ms, ok=page.success)
                    return idx, page
                except Exception as exc:
                    log.error("crawl_failed", url=url, error=str(exc))
                    return idx, FetchedPage(
                        url=url, url_normalized=url, success=False,
                        http_status=None, fetch_duration_ms=0,
                        html="", markdown="", title="", meta_description="",
                        error=str(exc),
                    )

        results = await asyncio.gather(*[_one(i, u) for i, u in enumerate(url_list)])

    results.sort(key=lambda r: r[0])
    return [p for _, p in results]
