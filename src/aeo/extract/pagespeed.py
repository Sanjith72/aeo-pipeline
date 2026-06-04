"""
Google PageSpeed Insights API client (criterion 8: Load Speed).

Free tier ~25k queries/day. Disabled when PSI_API_KEY is unset — the
scorer will fall back to a neutral score.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..logging import get_logger
from ..settings import get_settings

log = get_logger(__name__)

_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


async def fetch(url: str, strategy: str = "mobile") -> dict[str, Any] | None:
    s = get_settings()
    if not s.psi_api_key:
        return None
    params = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
    }
    # Authenticate via header, NOT a `key=` query param: keeping the secret out of
    # the request URL means it can never reach a log via an exception's str() (an
    # httpx.HTTPStatusError stringifies the full URL). Google APIs accept this.
    headers = {"x-goog-api-key": s.psi_api_key}
    from ..crawl.transport import async_transport

    try:
        async with httpx.AsyncClient(timeout=60, transport=async_transport()) as client:
            resp = await client.get(_ENDPOINT, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        # Log only the status code and exception type — never str(exc), which can
        # carry the request URL (and any query-string secret) verbatim.
        log.warning("psi_fetch_failed", url=url, status=_status_of(exc), error=type(exc).__name__)
        return None

    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    perf = categories.get("performance", {}).get("score")
    audits = lighthouse.get("audits", {})
    return {
        "strategy": strategy,
        "performance_score": round((perf or 0) * 100) if perf is not None else None,
        "lcp_ms": _numeric_value(audits.get("largest-contentful-paint")),
        "tbt_ms": _numeric_value(audits.get("total-blocking-time")),
        "cls":    _numeric_value(audits.get("cumulative-layout-shift")),
        "fcp_ms": _numeric_value(audits.get("first-contentful-paint")),
    }


def fetch_sync(url: str, strategy: str = "mobile") -> dict[str, Any] | None:
    """Sync wrapper for places that aren't running an event loop."""
    return asyncio.run(fetch(url, strategy))


def _status_of(exc: Exception) -> int | None:
    """HTTP status code from an httpx error, if it carries a response. Transport
    errors (timeout/connect) have no response → None."""
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def _numeric_value(audit: dict | None) -> float | None:
    if not audit:
        return None
    return audit.get("numericValue")
