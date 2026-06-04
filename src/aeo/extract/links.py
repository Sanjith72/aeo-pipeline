"""
Internal / external link analysis. External authority links feed
criterion 7 (E-E-A-T).
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from ..settings import load_yaml_file
from ..utils.url import absolute, host_of, same_site


def extract(html: str, soup: BeautifulSoup, url: str) -> dict[str, Any]:
    authority_domains = (
        load_yaml_file("scoring.yaml")
        .get("criteria", {})
        .get("citation_signals", {})
        .get("authority_domains", [])
    )

    internal: list[str] = []
    external: list[str] = []
    authority: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = (a["href"] or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = absolute(url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)

        if same_site(abs_url, url):
            internal.append(abs_url)
        else:
            external.append(abs_url)
            matched = _authority_domain(host_of(abs_url), authority_domains)
            if matched:
                authority.append({
                    "url": abs_url,
                    "domain": matched,
                    "text": a.get_text(" ", strip=True)[:200],
                })

    return {
        "internal": internal,
        "external": external,
        "authority": authority,
        "internal_count": len(internal),
        "external_count": len(external),
        "authority_count": len(authority),
        "authority_domains": sorted({a["domain"] for a in authority}),
    }


def _authority_domain(host: str, domains: list[str]) -> str | None:
    """Most specific configured domain a host belongs to, or None.

    Config can list overlapping domains (e.g. ``nist.gov`` and ``nvd.nist.gov``);
    a host that matches several is attributed to the longest — deterministic and
    independent of config ordering.
    """
    matches = [d for d in domains if host == d or host.endswith("." + d)]
    return max(matches, key=len) if matches else None
