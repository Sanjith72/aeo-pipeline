"""
Process-wide shared headless-Chromium pool for the intake-prefill fallback.

``playwright_fetch_text`` (discovery.py) renders a homepage with a real browser when the
cheap httpx fetch is blocked by a bot wall. Launching a fresh Chromium per request costs
~1.5s of startup each and, under a burst of blocked-site prefills, would spawn many browser
processes at once. This module keeps ONE long-lived browser alive and hands each render its
own browser *context* (an isolated, incognito-like profile, so one site's bot-challenge
cookies never leak into the next render). A bounded semaphore caps concurrent renders so the
fallback can't exhaust memory.

Lifecycle: the browser is launched lazily on first use, relaunched if it crashed or
disconnected, rebuilt if the event loop changed (so repeated ``asyncio.run`` calls in a
script/test don't reuse a browser bound to a dead loop), and closed by :func:`close_pool`
(wired into the API's shutdown). Everything degrades to ``None`` (never raises) when
Playwright/Chromium isn't installed, so callers fall back to empty facts exactly as before.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from ..logging import get_logger
from ..settings import get_settings

log = get_logger(__name__)

# Match the per-launch flags used before the pool: drop the headless-automation banner that
# some walls sniff. NO navigator.webdriver patch — that patch is itself a detection signal
# (it flipped zillow 200→403), so plain realistic Chromium is what we run.
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
_VIEWPORT = {"width": 1366, "height": 768}


class _BrowserPool:
    def __init__(self) -> None:
        self._pw: Any = None            # the Playwright driver (subprocess)
        self._browser: Any = None       # the shared Chromium instance
        self._loop: asyncio.AbstractEventLoop | None = None  # loop the above are bound to
        self._lock = asyncio.Lock()     # guards (re)launch / teardown
        self._sem: asyncio.Semaphore | None = None
        self._sem_loop: asyncio.AbstractEventLoop | None = None

    def _semaphore(self) -> asyncio.Semaphore:
        # Bound concurrent renders. A Semaphore binds to the running loop on first await, so
        # rebuild it if the loop changed (keeps repeated asyncio.run() calls safe).
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem_loop is not loop:
            self._sem = asyncio.Semaphore(max(1, get_settings().intake.playwright_pool_size))
            self._sem_loop = loop
        return self._sem

    async def _teardown_locked(self) -> None:
        """Best-effort close of the browser + driver. Called under ``self._lock``. Swallows
        errors — when the previous event loop is already dead, close() can't complete and we
        just drop the references."""
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None
        self._loop = None

    async def _ensure_browser(self) -> Any:
        """Return a live shared browser, launching (or relaunching) it as needed."""
        loop = asyncio.get_running_loop()
        if self._browser is not None and self._loop is loop and self._browser.is_connected():
            return self._browser
        async with self._lock:
            # Re-check under the lock (another coroutine may have just launched it).
            if self._browser is not None and self._loop is loop and self._browser.is_connected():
                return self._browser
            if self._loop is not None and self._loop is not loop:
                # Loop changed (e.g. a fresh asyncio.run) — the old driver/browser are bound
                # to a dead loop; drop them before relaunching on the current one.
                await self._teardown_locked()
            elif self._browser is not None:
                # Same loop but the browser died — close the husk before relaunching.
                await self._teardown_locked()
            from playwright.async_api import async_playwright

            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            self._loop = loop
            log.info("browser_pool_launched")
            return self._browser

    async def render(self, url: str, *, user_agent: str, timeout_ms: int) -> str | None:
        """Render ``url`` in an isolated context off the shared browser and return its
        post-JS HTML, or None on any failure / when Playwright isn't installed."""
        try:
            import playwright.async_api  # noqa: F401  — presence check (optional heavy dep)
        except Exception:
            log.info("playwright_unavailable", url=url)
            return None

        async with self._semaphore():
            ctx = None
            try:
                browser = await self._ensure_browser()
                ctx = await browser.new_context(
                    user_agent=user_agent, locale="en-US", viewport=_VIEWPORT
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                # Let a JS challenge resolve / content paint, but never block past the budget.
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=min(6000, timeout_ms))
                return await page.content()
            except Exception as exc:
                log.info("playwright_render_failed", url=url, error=str(exc))
                # If the browser died mid-render, drop it so the next call relaunches cleanly.
                if self._browser is not None and not self._browser.is_connected():
                    self._browser = None
                return None
            finally:
                if ctx is not None:
                    with contextlib.suppress(Exception):
                        await ctx.close()

    async def aclose(self) -> None:
        async with self._lock:
            await self._teardown_locked()


_POOL = _BrowserPool()


async def render_page(url: str, *, user_agent: str, timeout_ms: int) -> str | None:
    """Render ``url`` with the shared headless-Chromium pool. Returns post-JS HTML or None."""
    return await _POOL.render(url, user_agent=user_agent, timeout_ms=timeout_ms)


async def close_pool() -> None:
    """Close the shared browser + driver (wired into the API shutdown). Safe to call when the
    pool was never used."""
    await _POOL.aclose()
