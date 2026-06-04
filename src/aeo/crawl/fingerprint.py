"""Content-hash based skip-if-unchanged."""

from __future__ import annotations

from ..settings import get_settings
from ..storage.repos import pages as pages_repo


def should_skip(url_normalized: str, new_hash: str | None) -> bool:
    if not get_settings().crawler.fingerprint.enabled:
        return False
    if not new_hash:
        return False
    prev = pages_repo.last_hash(url_normalized)
    return prev is not None and prev == new_hash
