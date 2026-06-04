"""
Detect glossary / definition opportunities.

Per the audit notes, the Securin glossary has 132 definitions with zero
DefinedTerm schema. This extractor flags pages where adding DefinedTerm
schema would have outsized AEO impact.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    dt_dd_pairs = []
    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt")
        defs = dl.find_all("dd")
        for t, d in zip(terms, defs, strict=False):
            dt_dd_pairs.append({
                "term": t.get_text(" ", strip=True),
                "definition": d.get_text(" ", strip=True)[:280],
            })

    # Heading-as-definition pattern: an H3 followed by a paragraph of definition
    heading_defs = 0
    for h in soup.find_all(["h3", "h4"]):
        text = h.get_text(strip=True)
        if not text or len(text.split()) > 6:
            continue
        nxt = h.find_next_sibling()
        if nxt and nxt.name == "p":
            heading_defs += 1

    total = len(dt_dd_pairs) + heading_defs
    is_glossary_candidate = total >= 20

    has_defined_term_schema = False
    for s in soup.find_all("script", type="application/ld+json"):
        if "DefinedTerm" in (s.string or s.text or ""):
            has_defined_term_schema = True
            break

    return {
        "dl_pairs": dt_dd_pairs,
        "heading_definition_count": heading_defs,
        "total_definitions": total,
        "is_glossary_candidate": is_glossary_candidate,
        "has_defined_term_schema": has_defined_term_schema,
        "opportunity": is_glossary_candidate and not has_defined_term_schema,
    }
