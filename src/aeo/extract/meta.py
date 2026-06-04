"""Title, description, canonical, language."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""

    desc = _meta_content(soup, "description") or _meta_content(soup, "og:description") or ""
    canonical = _link_href(soup, "canonical") or ""
    lang = (soup.html.get("lang") if soup.html else "") or ""
    og_type = _meta_content(soup, "og:type") or ""

    return {
        "title": title,
        "description": desc,
        "canonical": canonical,
        "lang": lang,
        "og_type": og_type,
    }


def _meta_content(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
    return (el.get("content") or "").strip() if el else ""


def _link_href(soup: BeautifulSoup, rel: str) -> str:
    el = soup.find("link", rel=rel)
    return (el.get("href") or "").strip() if el else ""
