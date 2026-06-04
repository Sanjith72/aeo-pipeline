"""
Deterministic statistic extraction.

Regex-collects candidate hits, then prunes false positives (page numbers,
copyright years) and dedupes by normalized form.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..settings import load_yaml_file
from ..utils.html import body_text


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    cfg = load_yaml_file("extractors.yaml").get("stats", {})
    patterns: dict[str, re.Pattern] = {
        name: re.compile(pat) for name, pat in cfg.get("patterns", {}).items()
    }
    blacklist = [re.compile(p) for p in cfg.get("blacklist_patterns", [])]

    text = body_text(soup)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()

    for kind, pat in patterns.items():
        for m in pat.finditer(text):
            value = m.group(0).strip()
            if not value or any(b.search(value) for b in blacklist):
                continue
            key = f"{kind}:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            hits.append({"kind": kind, "value": value, "context": _context(text, m)})

    by_kind: dict[str, int] = {}
    for h in hits:
        by_kind[h["kind"]] = by_kind.get(h["kind"], 0) + 1

    return {
        "hits": hits,
        "count": len(hits),
        "by_kind": by_kind,
    }


def _context(text: str, m: re.Match, window: int = 60) -> str:
    start = max(0, m.start() - window)
    end = min(len(text), m.end() + window)
    return text[start:end].strip()
