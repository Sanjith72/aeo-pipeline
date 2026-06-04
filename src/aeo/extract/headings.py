"""
Heading extraction with hierarchy + question detection.

Used by:
  - scoring.heading_structure (criterion 5)
  - scoring.qa_blocks       (paired with adjacent paragraph)
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..settings import load_yaml_file

_QUESTION_END_RE = re.compile(r".+\?\s*$")


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    extractors_cfg = load_yaml_file("extractors.yaml")
    template_patterns = [re.compile(p) for p in extractors_cfg.get("template_h1_patterns", [])]

    headings: dict[str, list[str]] = {f"h{i}": [] for i in range(1, 7)}
    sequence: list[dict[str, Any]] = []

    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        tag = el.name
        text = el.get_text(separator=" ", strip=True)
        if not text:
            continue
        headings[tag].append(text)
        sequence.append({
            "tag": tag,
            "text": text,
            "is_question": bool(_QUESTION_END_RE.match(text)),
        })

    h1_list = headings["h1"]
    template_h1 = any(p.search(h) for h in h1_list for p in template_patterns)

    h23 = headings["h2"] + headings["h3"]
    question_h23 = sum(1 for h in h23 if _QUESTION_END_RE.match(h))

    return {
        "by_level": headings,
        "sequence": sequence,
        "counts": {f"h{i}": len(headings[f"h{i}"]) for i in range(1, 7)},
        "h1_text": h1_list[0] if h1_list else None,
        "h1_count": len(h1_list),
        "missing_h1": len(h1_list) == 0,
        "template_h1": template_h1,
        "h23_total": len(h23),
        "h23_question_count": question_h23,
        "h23_question_ratio": (question_h23 / len(h23)) if h23 else 0.0,
        "hierarchy_ok": _hierarchy_ok(sequence),
    }


def _hierarchy_ok(seq: list[dict[str, Any]]) -> bool:
    """No level skips downward (e.g., h2 → h4 with no h3)."""
    last = 0
    for h in seq:
        level = int(h["tag"][1])
        if last and level > last + 1:
            return False
        last = level
    return True
