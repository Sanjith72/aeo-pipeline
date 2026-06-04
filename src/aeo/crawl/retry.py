"""Tenacity policy shared by crawl + HTTP."""

from __future__ import annotations

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from ..settings import get_settings


def fetch_retry() -> AsyncRetrying:
    s = get_settings().crawler.retry
    return AsyncRetrying(
        stop=stop_after_attempt(s.max_attempts),
        wait=wait_random_exponential(
            multiplier=s.initial_backoff_sec,
            max=s.max_backoff_sec,
        ),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )


# Network exceptions we consider retryable. Anything else (parse errors, bad
# URLs) should fail fast.
class TransientError(Exception):
    pass


_RETRYABLE = (TransientError,)
