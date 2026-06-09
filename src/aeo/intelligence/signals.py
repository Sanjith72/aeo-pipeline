"""
Shared signal helpers for the intelligence layer.

The engines (classification, business_intent, journey) all reason over the same
input — the site's discovered, classified pages — by matching configurable tokens
against URL paths and counting page types. This module gives them one lightweight
view (:class:`PageView`) and one matching rule (:func:`token_hit`) so the matching
semantics are identical everywhere and decoupled from the prioritizer's
``ScoredUrl`` (so SP-2's no-website synthetic pages can feed the same engines).

Pure, dependency-light: a regex tokenizer + the shared slug normalizer. No I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..reference.blueprint import normalize_slug

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class PageView:
    """A discovered page as the intelligence engines see it."""

    url: str
    path: str  # normalized, lowercased path (e.g. "/solutions/ctem")
    page_type: str


def page_view(url: str, page_type: str) -> PageView:
    return PageView(url=url, path=normalize_slug(url), page_type=page_type)


def to_page_views(scored: Iterable[Any]) -> list[PageView]:
    """Convert prioritizer ``ScoredUrl`` rows (or anything with ``.url`` /
    ``.page_type``) into the engine view. Duck-typed on purpose."""
    return [page_view(s.url, getattr(s, "page_type", "default")) for s in scored]


def _words(path: str) -> set[str]:
    return set(_WORD_RE.findall(path.lower()))


def token_hit(path: str, token: str) -> bool:
    """Does a signal token match a path? Three forms:

    * a *prefix/stem* token ending in ``*`` (e.g. ``industr*``) matches any word that
      starts with the stem — so ``/industry``, ``/industries``, ``/industrial`` all fire;
    * a *phrase* token (one containing ``-`` or ``/``) matches as a substring;
    * a *single-word* token matches a whole word only — so ``shop`` does not fire on
      ``/workshop`` and ``law`` does not fire on ``/lawnmower``.

    ``_`` and ``-`` are treated as equivalent separators, so ``/case_studies`` matches a
    ``case-stud`` phrase token exactly as ``/case-studies`` does. Mirrors the
    word-boundary discipline in ``reference.taxonomic_ceiling.resolve_ceiling``.
    """
    t = token.strip().lower()
    if not t:
        return False
    norm = path.lower().replace("_", "-")
    if t.endswith("*"):
        stem = t[:-1]
        return bool(stem) and any(w.startswith(stem) for w in _words(norm))
    if "-" in t or "/" in t:
        return t in norm
    return t in _words(norm)


def fired_tokens(paths: list[str], tokens: Iterable[str]) -> list[str]:
    """The distinct tokens that match across any path — used as human-readable
    evidence. Order-stable on the token iterable."""
    return [tok for tok in tokens if any(token_hit(p, tok) for p in paths)]
