"""
Site facts — derive **Location**, **what-you-offer (services)**, and best-effort
**on-site competitor** signals from a quick crawl of the homepage + a few key pages.

The URL-first intake infers these instead of asking, so the wizard's "About you" step
arrives prefilled. Everything here is pure and offline-testable: the extraction works on
already-fetched ``(url, html)`` pairs, and the network gatherer takes an injectable fetch
callable. No LLM is involved — competitors are mined from the site's own
comparison/alternatives pages (best-effort; an empty result is the common, correct
outcome). Location/services lean on schema.org JSON-LD first, then light heuristics.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..extract import links as links_extractor
from ..extract import schema_jsonld
from ..logging import get_logger
from ..utils.html import parse
from ..utils.url import absolute, host_of, normalize, same_site
from .industry import classify_vertical
from .intake import infer_location
from .signals import to_page_views

log = get_logger(__name__)

# Returns the page body (HTML) or None on any failure / non-200.
FetchText = Callable[[str], Awaitable["str | None"]]

# Pages worth fetching beyond the homepage — where address + offerings usually live.
_KEY_PATH_RE = re.compile(
    r"/(about|contact|locations?|services?|products?|solutions?|what-we-do|menu)(/|$|\.)", re.I
)
# Conventional paths to try even when the homepage doesn't link them by a tidy slug.
_GUESS_PATHS = ("/about", "/contact", "/services", "/products")
_MAX_PAGES = 4  # homepage + up to three key pages

# "City, ST 12345" — a US-style address line. City is a run of Capitalized words only
# (so it doesn't swallow lowercase lead-in prose); the ZIP keeps false positives down.
_CITY_ST_ZIP = re.compile(
    r"([A-Z][a-zA-Z]+(?:[ '\-][A-Z][a-zA-Z]+){0,3}),\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?"
)

# Comparison/alternative signals that name a competitor on the site's own pages.
_VS_RE = re.compile(r"(?:^|[/\s\-_])(?:vs\.?|versus)[/\s\-_]+([A-Za-z0-9][A-Za-z0-9.\-_& ]{1,40})", re.I)
_ALT_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9.\-_& ]{1,40})[\s\-_]+alternatives?\b", re.I)

# Nav labels that are never a "service you offer".
_SERVICE_STOPWORDS = frozenset({
    "home", "about", "about us", "contact", "contact us", "blog", "news", "pricing",
    "login", "log in", "sign in", "sign up", "get started", "book now", "careers",
    "privacy", "terms", "faq", "faqs", "resources", "support", "search", "menu",
    "services", "products", "solutions", "our services", "our work", "portfolio",
    "team", "reviews", "testimonials", "gallery", "shop", "cart", "account",
})
_MAX_SERVICES = 8
_MAX_COMPETITORS = 6


@dataclass(slots=True)
class SiteFacts:
    location: str | None = None
    services: list[str] = field(default_factory=list)
    competitors: list[dict[str, str]] = field(default_factory=list)
    # Crawl-derived specific vertical (Healthcare, Finance, …) — the fallback when
    # Wikidata has no entity for this site. None when the content gives no clear signal.
    industry: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "services": self.services,
            "competitors": self.competitors,
            "industry": self.industry,
        }


@dataclass(slots=True)
class FetchedDoc:
    url: str
    html: str


# ── pure extraction (no network) ────────────────────────────────────────────────


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _type_set(obj: dict) -> set[str]:
    return {str(t).lower() for t in _as_list(obj.get("@type"))}


def _name_of(value: Any) -> str | None:
    """The ``name`` of a schema node, or the node itself when it's a bare string."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _format_address(addr: Any) -> str | None:
    if isinstance(addr, list):
        addr = addr[0] if addr else None
    if isinstance(addr, dict):
        city = str(addr.get("addressLocality") or "").strip()
        region = str(addr.get("addressRegion") or "").strip()
        country = str(addr.get("addressCountry") or "").strip() if isinstance(addr.get("addressCountry"), str) else ""
        if city and region:
            return f"{city}, {region}"
        return city or region or country or None
    if isinstance(addr, str) and addr.strip():
        return addr.strip()[:120]
    return None


def location_from_blocks(blocks: list[dict]) -> str | None:
    """Location from schema.org JSON-LD: a postal ``address`` first, then ``areaServed``."""
    for obj in blocks:
        loc = _format_address(obj.get("address"))
        if loc:
            return loc
    for obj in blocks:
        for area in _as_list(obj.get("areaServed")):
            name = _name_of(area)
            if name:
                return name
    return None


def location_from_text(text: str) -> str | None:
    m = _CITY_ST_ZIP.search(text)
    return f"{m.group(1).strip()}, {m.group(2)}" if m else None


def services_from_blocks(blocks: list[dict]) -> list[str]:
    """Offerings from JSON-LD: Service/Product names, OfferCatalog + makesOffer items."""
    out: list[str] = []
    for obj in blocks:
        types = _type_set(obj)
        if ("service" in types or "product" in types) and _name_of(obj):
            out.append(_name_of(obj))  # type: ignore[arg-type]
        for cat in _as_list(obj.get("hasOfferCatalog")):
            if isinstance(cat, dict):
                for el in _as_list(cat.get("itemListElement")):
                    name = _name_of(el.get("itemOffered") if isinstance(el, dict) else None) or _name_of(el)
                    if name:
                        out.append(name)
        for off in _as_list(obj.get("makesOffer")):
            name = _name_of(off.get("itemOffered") if isinstance(off, dict) else None)
            if name:
                out.append(name)
    return out


def _clean_service(label: str) -> str | None:
    label = re.sub(r"\s+", " ", label).strip(" \t\n·|-—–»")
    if not label or len(label) > 48 or label.lower() in _SERVICE_STOPWORDS:
        return None
    if not re.search(r"[A-Za-z]", label):
        return None
    return label


def _is_service_link(url: str) -> bool:
    # A link INTO a services/products/solutions section names a specific offering; the
    # anchor text alone is too noisy to trust without the path signal.
    return bool(re.search(r"/(services?|products?|solutions?|treatments?)/", url.lower()))


def services_from_links(docs: list[FetchedDoc], own: str) -> list[str]:
    """Service names from links that point INTO a /services|/products|/solutions section
    (the anchor text names the offering), e.g. ``/services/teeth-whitening`` → its label."""
    out: list[str] = []
    for doc in docs:
        soup = parse(doc.html)
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            abs_url = absolute(doc.url, href)
            if not same_site(abs_url, own):
                continue
            if _is_service_link(abs_url):
                cleaned = _clean_service(a.get_text(" ", strip=True))
                if cleaned:
                    out.append(cleaned)
    return out


def _dedupe_keep_order(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def _competitor_name(raw: str) -> str | None:
    name = re.sub(r"[\-_]+", " ", raw).strip()
    name = re.sub(r"\s+", " ", name)
    # Drop trailing filler the regexes can sweep in ("acme pricing", "acme review").
    name = re.sub(r"\b(pricing|review|reviews|comparison|features?|page)\b.*$", "", name, flags=re.I).strip()
    if not name or len(name) < 2 or len(name) > 40:
        return None
    if name.lower() in {"the", "our", "your", "best", "top", "other", "competitors", "us"}:
        return None
    return name.title()


def competitors_from_docs(docs: list[FetchedDoc], own: str) -> list[dict[str, str]]:
    """Best-effort, no-LLM: mine the site's own comparison / alternatives pages for the
    OTHER party's name (e.g. ``/compare/usvs-acme``, "Acme alternative", "X vs Y")."""
    own_root = host_of(own).removeprefix("www.").split(".")[0].lower()
    found: list[str] = []
    for doc in docs:
        soup = parse(doc.html)
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        haystacks = [doc.url, title]
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "")
            if re.search(r"vs|versus|alternativ|compar", href, re.I):
                haystacks.append(absolute(doc.url, href))
                haystacks.append(a.get_text(" ", strip=True))
        for h in haystacks:
            if not h:
                continue
            for m in _VS_RE.finditer(h):
                name = _competitor_name(m.group(1))
                if name and name.lower() != own_root:
                    found.append(name)
            for m in _ALT_RE.finditer(h):
                name = _competitor_name(m.group(1))
                if name and name.lower() != own_root:
                    found.append(name)
    names = _dedupe_keep_order(found, _MAX_COMPETITORS)
    return [{"name": n, "domain": ""} for n in names]


def extract_facts(docs: list[FetchedDoc], *, domain: str) -> SiteFacts:
    """Fold the fetched pages into a :class:`SiteFacts` (pure; no network)."""
    own = normalize(docs[0].url) if docs else f"https://{domain}/"
    blocks: list[dict] = []
    text_parts: list[str] = []
    internal_urls: list[str] = []
    for doc in docs:
        soup = parse(doc.html)
        sj = schema_jsonld.extract(doc.html, soup, doc.url)
        blocks.extend(sj.get("blocks", []))
        text_parts.append(soup.get_text(" ", strip=True))
        lk = links_extractor.extract(doc.html, soup, doc.url)
        internal_urls.extend(lk.get("internal", []))

    location = (
        location_from_blocks(blocks)
        or location_from_text(" ".join(text_parts))
        or infer_location(to_page_views(_url_views(internal_urls + [d.url for d in docs])))
    )
    services = _dedupe_keep_order(
        [s for s in (_clean_service(x) for x in services_from_blocks(blocks)) if s]
        + services_from_links(docs, own),
        _MAX_SERVICES,
    )
    competitors = competitors_from_docs(docs, own)
    industry = classify_vertical(" ".join(text_parts), services=services)
    return SiteFacts(location=location, services=services, competitors=competitors, industry=industry)


def _url_views(urls: list[str]) -> list[Any]:
    from types import SimpleNamespace

    return [SimpleNamespace(url=u, page_type="default") for u in urls]


# ── network gatherer (injectable fetch) ─────────────────────────────────────────


def _select_key_pages(homepage_url: str, homepage_html: str, *, limit: int) -> list[str]:
    """Same-site links that look like about/contact/services pages, plus conventional
    guesses, deduped and capped."""
    soup = parse(homepage_html)
    picks: list[str] = []
    seen: set[str] = {normalize(homepage_url)}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        abs_url = normalize(absolute(homepage_url, href))
        if abs_url in seen or not same_site(abs_url, homepage_url):
            continue
        if _KEY_PATH_RE.search(abs_url):
            seen.add(abs_url)
            picks.append(abs_url)
        if len(picks) >= limit:
            break
    for guess in _GUESS_PATHS:
        if len(picks) >= limit:
            break
        g = normalize(absolute(homepage_url, guess))
        if g not in seen:
            seen.add(g)
            picks.append(g)
    return picks[:limit]


async def gather_site_facts(
    domain: str, *, fetch: FetchText | None = None, max_pages: int = _MAX_PAGES
) -> SiteFacts:
    """Fetch the homepage + a few key pages and derive :class:`SiteFacts`. Best-effort:
    an unreachable homepage (or any error) yields empty facts rather than raising, so the
    wizard prefill never blocks the flow."""
    from ..crawl.discovery import _default_fetch_text, seed_url

    fetch = fetch or _default_fetch_text
    home = seed_url(domain)
    try:
        home_html = await fetch(home)
    except Exception as exc:  # network hiccup → empty facts, never fatal
        log.warning("site_facts_homepage_failed", domain=domain, error=str(exc))
        return SiteFacts()
    if not home_html:
        return SiteFacts()

    docs = [FetchedDoc(url=home, html=home_html)]
    for url in _select_key_pages(home, home_html, limit=max(0, max_pages - 1)):
        try:
            html = await fetch(url)
        except Exception:
            html = None
        if html:
            docs.append(FetchedDoc(url=url, html=html))

    try:
        return extract_facts(docs, domain=domain)
    except Exception as exc:  # extraction must never break intake
        log.warning("site_facts_extract_failed", domain=domain, error=str(exc))
        return SiteFacts()
