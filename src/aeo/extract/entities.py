"""
Entity / first-person mention counting.

Drives criterion 4 (Entity Consistency). Counts come from the body text
only (chrome already stripped) so footer brand mentions don't skew the ratio.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..settings import load_yaml_file
from ..utils.html import body_text
from ..utils.url import host_of


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    cfg = load_yaml_file("entities.yaml").get("entities", {})
    if not cfg:
        return {"detected": [], "primary": None, "entity_count": 0, "first_person_count": 0, "ratio": None}

    text = body_text(soup)
    host = host_of(url)

    # Choose the primary entity for this page based on the host domain
    primary_key = None
    for key, ent in cfg.items():
        if ent.get("domain") and host.endswith(ent["domain"]):
            primary_key = key
            break

    detected: list[dict[str, Any]] = []
    for key, ent in cfg.items():
        canonical = ent.get("canonical", key)
        aliases = [canonical, *ent.get("aliases", [])]
        c = sum(_count_word(text, a) for a in aliases)
        detected.append({"name": canonical, "count": c, "is_primary": key == primary_key})

    primary = next((d for d in detected if d["is_primary"]), None)

    first_person_count = 0
    if primary_key:
        fps = cfg[primary_key].get("first_person", [])
        first_person_count = sum(_count_word(text, w) for w in fps)

    entity_count = primary["count"] if primary else 0
    ratio = (entity_count / first_person_count) if first_person_count > 0 else None

    return {
        "detected": detected,
        "primary": primary,
        "entity_count": entity_count,
        "first_person_count": first_person_count,
        "ratio": ratio,
    }


def _count_word(text: str, term: str) -> int:
    """Whole-word, case-insensitive count. Handles multi-word terms."""
    if not term:
        return 0
    pat = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    return len(pat.findall(text))
