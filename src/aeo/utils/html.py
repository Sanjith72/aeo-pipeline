"""HTML parsing helpers shared across extractors."""

from __future__ import annotations

from bs4 import BeautifulSoup

# Tags whose text is structural/chrome, not content
CHROME_TAGS = ("script", "style", "nav", "footer", "header", "aside", "noscript", "iframe")


def parse(html: str) -> BeautifulSoup:
    """Parse with lxml, fall back to html.parser on lxml hiccup."""
    if not html:
        return BeautifulSoup("", "html.parser")
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def body_text(soup: BeautifulSoup) -> str:
    """Plain body text with nav/footer/script stripped. Destructive on the soup."""
    for tag in soup(CHROME_TAGS):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def first_text(soup: BeautifulSoup, selector: str) -> str | None:
    el = soup.select_one(selector)
    return el.get_text(strip=True) if el else None
