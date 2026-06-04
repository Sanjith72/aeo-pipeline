"""Protocol for extractors. Each one is a pure function."""

from __future__ import annotations

from typing import Any, Protocol

from bs4 import BeautifulSoup


class Extractor(Protocol):
    def __call__(self, html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]: ...
