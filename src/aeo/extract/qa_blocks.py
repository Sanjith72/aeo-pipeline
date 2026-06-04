"""
Detect real Q→A pairs.

A heading that ends with '?' AND is followed (within the next 2 sibling
elements) by a paragraph of at least N characters counts as a Q&A pair.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

from ..settings import load_yaml_file


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    cfg = load_yaml_file("scoring.yaml").get("criteria", {}).get("qa_blocks", {})
    min_answer = int(cfg.get("min_answer_chars", 80))
    question_words = {w.lower() for w in cfg.get("question_words", [])}

    pairs: list[dict[str, Any]] = []
    question_heading_count = 0

    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = heading.get_text(separator=" ", strip=True)
        if not text:
            continue
        first_word = text.split()[0].lower() if text.split() else ""
        ends_q = text.endswith("?")
        starts_q = first_word in question_words

        if not (ends_q or starts_q):
            continue

        question_heading_count += 1
        answer = _next_paragraph(heading)
        if answer and len(answer) >= min_answer:
            pairs.append({
                "question": text,
                "answer_preview": answer[:280],
                "answer_chars": len(answer),
                "heading_tag": heading.name,
            })

    return {
        "qa_pairs": pairs,
        "pair_count": len(pairs),
        "question_heading_count": question_heading_count,
    }


def _next_paragraph(node: Tag) -> str:
    """Concatenate text of up to 2 following siblings, stopping at the next heading."""
    out: list[str] = []
    cur: Tag | None = node.find_next_sibling()
    hops = 0
    while cur is not None and hops < 3:
        if isinstance(cur, Tag):
            if cur.name and cur.name.startswith("h") and len(cur.name) == 2 and cur.name[1].isdigit():
                break
            text = cur.get_text(separator=" ", strip=True)
            if text:
                out.append(text)
                if sum(len(t) for t in out) >= 200:
                    break
        cur = cur.find_next_sibling()
        hops += 1
    return " ".join(out).strip()
