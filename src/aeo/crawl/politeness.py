"""
Robots.txt awareness + per-host token bucket rate limiting.

Both are cheap to implement and prevent the crawler from getting banned at
scale. Robots cache is per-process; the bucket is per-host, per-process.
"""

from __future__ import annotations

import asyncio
import time
import urllib.robotparser
from dataclasses import dataclass

import httpx

from ..logging import get_logger
from ..settings import get_settings
from ..utils.url import host_of

log = get_logger(__name__)


# ── Robots ──────────────────────────────────────────────────────────────────


class RobotsCache:
    def __init__(self) -> None:
        self._parsers: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._lock = asyncio.Lock()

    async def allowed(self, url: str, user_agent: str) -> bool:
        if not get_settings().crawler.respect_robots:
            return True
        host = host_of(url)
        async with self._lock:
            rp = self._parsers.get(host)
            if rp is None:
                rp = await self._load(host)
                self._parsers[host] = rp
        return rp.can_fetch(user_agent, url)

    async def _load(self, host: str) -> urllib.robotparser.RobotFileParser:
        rp = urllib.robotparser.RobotFileParser()
        url = f"https://{host}/robots.txt"
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.parse([])   # missing robots.txt → allow everything
        except Exception as exc:
            log.warning("robots_fetch_failed", host=host, error=str(exc))
            rp.parse([])
        return rp


# ── Token bucket per host ───────────────────────────────────────────────────


@dataclass
class _Bucket:
    capacity: int
    tokens: float
    refill_per_sec: float
    last: float


class RateLimiter:
    def __init__(self) -> None:
        s = get_settings().crawler.rate_limit
        self._capacity = s.burst
        self._refill = s.requests_per_minute / 60.0
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, url: str) -> None:
        host = host_of(url)
        while True:
            async with self._lock:
                bucket = self._buckets.get(host)
                now = time.monotonic()
                if bucket is None:
                    bucket = _Bucket(
                        capacity=self._capacity,
                        tokens=self._capacity,
                        refill_per_sec=self._refill,
                        last=now,
                    )
                    self._buckets[host] = bucket
                else:
                    elapsed = now - bucket.last
                    bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_per_sec)
                    bucket.last = now

                if bucket.tokens >= 1.0:
                    bucket.tokens -= 1.0
                    return
                sleep_for = (1.0 - bucket.tokens) / bucket.refill_per_sec
            await asyncio.sleep(sleep_for)
