"""Content fingerprinting — used to skip recrawls when nothing changed."""

from __future__ import annotations

import hashlib

from .text import collapse_whitespace


def content_hash(html: str, algorithm: str = "sha256") -> str:
    """Hash whitespace-normalized content. Whitespace-only churn doesn't trip it."""
    h = hashlib.new(algorithm)
    h.update(collapse_whitespace(html or "").encode("utf-8", errors="replace"))
    return h.hexdigest()
