"""
Passage-level chunking for downstream embedding / RAG.

Strategy: walk the heading sequence, group paragraphs under their nearest
heading into chunks of ~targets size (chars).
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag


def extract(html: str, soup: BeautifulSoup, url: str, target_chars: int = 1200) -> dict[str, Any]:
    chunks: list[dict[str, Any]] = []
    current_heading: str | None = None
    current_buf: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current_buf, current_len
        if not current_buf:
            return
        chunks.append({
            "heading": current_heading,
            "text": " ".join(current_buf).strip(),
            "chars": current_len,
            "index": len(chunks),
        })
        current_buf = []
        current_len = 0

    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        if not isinstance(el, Tag):
            continue
        if el.name and el.name.startswith("h"):
            flush()
            current_heading = el.get_text(" ", strip=True)
            continue

        text = el.get_text(" ", strip=True)
        if not text:
            continue
        current_buf.append(text)
        current_len += len(text)
        if current_len >= target_chars:
            flush()

    flush()
    return {
        "chunks": chunks,
        "chunk_count": len(chunks),
        "total_chars": sum(c["chars"] for c in chunks),
    }
