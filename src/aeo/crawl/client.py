"""
Crawl4AI wrapper. Single browser per CrawlClient instance — reuse cuts
~1.5s startup per page.

Lifecycle:
    async with CrawlClient() as client:
        page = await client.fetch(url)
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..logging import get_logger
from ..settings import get_settings
from ..storage.models import FetchedPage
from ..utils.hashing import content_hash
from ..utils.timing import stopwatch
from ..utils.url import normalize
from .politeness import RateLimiter, RobotsCache
from .retry import TransientError

log = get_logger(__name__)

# Lazy import — Crawl4AI pulls in Playwright; tests don't need it
_AsyncWebCrawler: Any = None
_CrawlerRunConfig: Any = None


def _import_crawl4ai() -> None:
    global _AsyncWebCrawler, _CrawlerRunConfig
    if _AsyncWebCrawler is None:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig  # type: ignore
        _AsyncWebCrawler = AsyncWebCrawler
        _CrawlerRunConfig = CrawlerRunConfig


class CrawlClient:
    def __init__(self) -> None:
        self._crawler: Any = None
        self._robots = RobotsCache()
        self._limiter = RateLimiter()
        self._cfg = get_settings().crawler

    async def __aenter__(self) -> CrawlClient:
        _import_crawl4ai()
        self._crawler = _AsyncWebCrawler(verbose=False, headless=self._cfg.browser.headless)
        await self._crawler.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._crawler is not None:
            await self._crawler.__aexit__(exc_type, exc, tb)
            self._crawler = None

    async def fetch(self, url: str) -> FetchedPage:
        norm = normalize(url)

        if not await self._robots.allowed(url, self._cfg.user_agent):
            log.info("robots_disallowed", url=url)
            return FetchedPage(
                url=url, url_normalized=norm, success=False,
                http_status=None, fetch_duration_ms=0,
                html="", markdown="", title="", meta_description="",
                error="blocked_by_robots_txt",
            )

        await self._limiter.acquire(url)
        return await self._fetch_once(url, norm)

    async def _fetch_once(self, url: str, url_normalized: str) -> FetchedPage:
        run_cfg = _CrawlerRunConfig(
            only_text=False,
            remove_overlay_elements=self._cfg.browser.remove_overlay_elements,
            word_count_threshold=self._cfg.browser.word_count_threshold,
        )

        with stopwatch() as t:
            try:
                result = await asyncio.wait_for(
                    self._crawler.arun(url=url, config=run_cfg),
                    timeout=self._cfg.request_timeout_sec,
                )
            except TimeoutError:
                return FetchedPage(
                    url=url, url_normalized=url_normalized, success=False,
                    http_status=None, fetch_duration_ms=int(t["elapsed_ms"]),
                    html="", markdown="", title="", meta_description="",
                    error=f"timeout_after_{self._cfg.request_timeout_sec}s",
                )
            except Exception as exc:
                # Treat unknown failures as transient unless caller knows better
                raise TransientError(str(exc)) from exc

        return _to_fetched(result, url, url_normalized, int(t["elapsed_ms"]))


def _to_fetched(result: Any, url: str, url_normalized: str, ms: int) -> FetchedPage:
    success = bool(getattr(result, "success", True))
    html = getattr(result, "html", "") or getattr(result, "cleaned_html", "") or ""

    md_raw = getattr(result, "markdown", "")
    if isinstance(md_raw, str):
        markdown = md_raw
    else:
        markdown = (
            getattr(md_raw, "fit_markdown", None)
            or getattr(md_raw, "raw_markdown", None)
            or str(md_raw or "")
        )

    meta = getattr(result, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {"title": getattr(meta, "title", ""), "description": getattr(meta, "description", "")}

    title = (meta.get("title") or meta.get("ogTitle") or "")[:512]
    desc = meta.get("description") or meta.get("ogDescription") or ""
    status = meta.get("statusCode") or getattr(result, "status_code", None)
    err = getattr(result, "error_message", None) if not success else None

    return FetchedPage(
        url=url, url_normalized=url_normalized, success=success,
        http_status=status, fetch_duration_ms=ms,
        html=html, markdown=markdown, title=title, meta_description=desc,
        error=err,
        content_hash=content_hash(html) if html else None,
    )
