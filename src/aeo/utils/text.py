"""Text helpers — whitespace, word counts, sentence splitting."""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\b[\w\-]+\b")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def collapse_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def sentence_split(text: str) -> list[str]:
    text = collapse_whitespace(text)
    if not text:
        return []
    return [s.strip() for s in _SENT_RE.split(text) if s.strip()]


def truncate(text: str, max_chars: int, suffix: str = "…") -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)] + suffix
