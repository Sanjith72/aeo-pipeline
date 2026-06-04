"""Flesch reading ease + sentence statistics. Used by content depth scorer."""

from __future__ import annotations

from typing import Any

import textstat
from bs4 import BeautifulSoup

from ..utils.html import body_text
from ..utils.text import sentence_split, word_count


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    text = body_text(soup)
    wc = word_count(text)
    sentences = sentence_split(text)
    sent_count = len(sentences)

    if wc < 50:
        return {
            "word_count": wc,
            "sentence_count": sent_count,
            "flesch_reading_ease": None,
            "flesch_kincaid_grade": None,
            "avg_sentence_length": None,
        }

    return {
        "word_count": wc,
        "sentence_count": sent_count,
        "flesch_reading_ease": round(textstat.flesch_reading_ease(text), 2),
        "flesch_kincaid_grade": round(textstat.flesch_kincaid_grade(text), 2),
        "avg_sentence_length": round(wc / max(sent_count, 1), 2),
    }
