"""
JSON-LD extraction with @type catalog and validity tracking.

Feeds criterion 1 (Schema Markup). Also exposes lists of authors and dates
which eeat.py can fall back to.
"""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    blocks: list[dict] = []
    invalid = 0
    types: list[str] = []
    authors: list[str] = []
    dates: list[str] = []

    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.text or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            invalid += 1
            continue
        for item in _iter_objects(parsed):
            blocks.append(item)
            t = item.get("@type")
            if isinstance(t, list):
                types.extend(str(x) for x in t)
            elif t:
                types.append(str(t))
            _collect_authors(item, authors)
            _collect_dates(item, dates)

    return {
        "block_count": len(blocks),
        "invalid_blocks": invalid,
        "types": sorted(set(types)),
        "type_counts": _counts(types),
        "authors": authors,
        "dates": dates,
        "blocks": blocks,
    }


def _iter_objects(node: Any):
    if isinstance(node, dict):
        yield node
        if isinstance(node.get("@graph"), list):
            for sub in node["@graph"]:
                yield from _iter_objects(sub)
    elif isinstance(node, list):
        for sub in node:
            yield from _iter_objects(sub)


def _counts(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


def _collect_authors(obj: dict, out: list[str]) -> None:
    for key in ("author", "creator"):
        val = obj.get(key)
        if isinstance(val, dict):
            name = val.get("name")
            if name:
                out.append(str(name))
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, dict) and v.get("name"):
                    out.append(str(v["name"]))
        elif isinstance(val, str):
            out.append(val)


def _collect_dates(obj: dict, out: list[str]) -> None:
    for key in ("datePublished", "dateModified", "uploadDate"):
        val = obj.get(key)
        if val:
            out.append(str(val))
