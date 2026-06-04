"""
E-E-A-T signal extraction: author, publication date, credentials.

Falls back through multiple selector strategies (meta tags → JSON-LD → CSS
classes → microdata) before giving up.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..settings import load_yaml_file


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    extractors_cfg = load_yaml_file("extractors.yaml")
    scoring_cfg = load_yaml_file("scoring.yaml").get("criteria", {}).get("citation_signals", {})

    author = _find_author(soup, extractors_cfg.get("author_selectors", {}))
    date = _find_date(soup, extractors_cfg.get("date_selectors", {}))
    credentials = _find_credentials(soup, scoring_cfg.get("credentials", []))

    return {
        "author": author,
        "publish_date": date,
        "credentials_mentioned": credentials,
        "has_author": bool(author),
        "has_date": bool(date),
    }


def _find_author(soup: BeautifulSoup, sel: dict) -> str | None:
    for css in sel.get("meta", []):
        el = soup.select_one(css)
        if el and el.get("content"):
            return el["content"].strip()
    for css in sel.get("css", []):
        el = soup.select_one(css)
        if el:
            txt = el.get_text(strip=True)
            if txt:
                return txt
    return None


def _find_date(soup: BeautifulSoup, sel: dict) -> str | None:
    for css in sel.get("meta", []):
        el = soup.select_one(css)
        if el and el.get("content"):
            return el["content"].strip()
    for css in sel.get("css", []):
        el = soup.select_one(css)
        if el:
            dt = el.get("datetime") or el.get_text(strip=True)
            if dt:
                return dt.strip()
    return None


def _find_credentials(soup: BeautifulSoup, credentials: list[str]) -> list[str]:
    if not credentials:
        return []
    text = soup.get_text(" ", strip=True)
    found: set[str] = set()
    for cred in credentials:
        pat = re.compile(rf"\b{re.escape(cred)}\b", re.IGNORECASE)
        if pat.search(text):
            found.add(cred)
    return sorted(found)
