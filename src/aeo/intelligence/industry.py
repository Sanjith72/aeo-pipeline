"""
Industry resolution — map a site to a *specific* vertical (Healthcare, Finance,
Restaurants, …) instead of the generic coarse label.

Two sources, tried in order by the caller:

  1. **Wikidata** (:func:`resolve_wikidata_industry`) — the deterministic, no-LLM path.
     Resolve the site by its official website (P856) to a Wikidata item, read the
     ``industry`` (P452) and ``instance of`` (P31) labels, drop generic classes
     ("business enterprise", "company", …), and map what's left to a vertical.
  2. **Crawl text** (:func:`classify_vertical`) — a keyword classifier over the crawled
     homepage text / services / topic, for sites with no Wikidata entity.

Both funnel through one ordered keyword map (:func:`match_vertical`) so a raw label and a
chunk of page text resolve to the same controlled vocabulary. Pure + offline-testable:
the SPARQL resolver takes an injectable fetch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..logging import get_logger
from ..utils.url import host_of

log = get_logger(__name__)

# Wikidata classes too generic to be a useful industry — exactly the "Enterprise" leak.
GENERIC_LABELS = frozenset({
    "enterprise", "business enterprise", "business", "company", "organization",
    "organisation", "corporation", "public company", "private company",
    "privately held company", "privately held", "brand", "legal entity", "subsidiary",
    "conglomerate", "holding company", "joint-stock company", "limited company",
    "for-profit corporation", "commercial organization", "economic activity",
})

# Ordered (most specific first) keyword → vertical map. A keyword matches as a substring
# of a lowercased raw label / text token, so "health care", "hospital", "medical clinic"
# all land on Healthcare. Order matters: the first matching vertical wins.
_KEYWORD_VERTICALS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cybersecur", "security software", "computer security", "infosec", "information security"), "Cybersecurity"),
    (("hospital", "health care", "healthcare", "medical", "clinic", "dental", "dentist",
      "pharmaceutic", "biotech", "physician", "nursing", "therapy"), "Healthcare"),
    (("insurance", "insurer"), "Insurance"),
    (("bank", "banking", "financial service", "finance", "investment", "asset management",
      "fintech", "credit union", "lending", "mortgage", "wealth management", "brokerage"), "Finance"),
    (("real estate", "realty", "realtor", "property management", "property developer"), "Real Estate"),
    (("restaurant", "fast food", "cafe", "café", "catering", "food service", "diner", "bistro"), "Restaurants"),
    (("e-commerce", "ecommerce", "online store", "online shop", "marketplace", "online retail"), "E-commerce"),
    (("retail", "supermarket", "grocery", "department store", "retailer"), "Retail"),
    (("law firm", "legal service", "attorney", "lawyer", "litigation", "law practice"), "Legal"),
    (("education", "school", "university", "college", "academy", "e-learning", "edtech", "tutoring"), "Education"),
    (("software", "saas", "information technology", "cloud computing", "internet company",
      "technology company", "computer software", "web development", "it service"), "Software / SaaS"),
    (("manufactur", "industrial", "factory", "fabrication"), "Manufacturing"),
    (("construction", "contractor", "builder", "roofing", "plumbing", "hvac", "remodeling"), "Construction"),
    (("hotel", "hospitality", "tourism", "travel agency", "airline", "resort", "lodging"), "Hospitality / Travel"),
    (("marketing", "advertising", "public relations", "seo agency", "digital agency", "branding"), "Marketing / Advertising"),
    (("media", "publishing", "newspaper", "broadcast", "magazine", "entertainment", "film", "music"), "Media / Publishing"),
    (("automotive", "automobile", "car dealer", "vehicle", "auto repair"), "Automotive"),
    (("non-profit", "nonprofit", "not-for-profit", "charity", "charitable", "ngo", "foundation"), "Nonprofit"),
    (("energy", "oil and gas", "petroleum", "utility", "renewable", "solar", "electric utility"), "Energy"),
    (("telecommunication", "telecom", "wireless carrier", "mobile network"), "Telecommunications"),
    (("logistics", "transportation", "shipping", "freight", "courier", "delivery service", "trucking"), "Logistics / Transportation"),
    (("agricultur", "farming", "farm"), "Agriculture"),
    (("beauty", "cosmetic", "salon", "spa", "skincare", "wellness"), "Beauty / Wellness"),
    (("fitness", "gym", "personal training", "yoga studio"), "Fitness"),
    (("food", "beverage", "brewery", "winery", "distillery", "bakery"), "Food & Beverage"),
    (("consulting", "consultancy", "professional service", "advisory"), "Professional services"),
    (("government", "public sector", "municipal"), "Government"),
)


def match_vertical(text: str) -> str | None:
    """Map a raw label / chunk of text to a specific vertical, or None. Generic-only
    text (e.g. "business enterprise") yields None so the caller falls through."""
    if not text:
        return None
    low = text.lower()
    if low.strip() in GENERIC_LABELS:
        return None
    for keywords, vertical in _KEYWORD_VERTICALS:
        if any(k in low for k in keywords):
            return vertical
    return None


def map_wikidata_labels(industry_labels: list[str], instance_labels: list[str]) -> str | None:
    """Resolve Wikidata P452 (industry) + P31 (instance of) labels to a vertical.
    Industry labels are tried first (they're the truest signal); generic classes are
    skipped via :func:`match_vertical`."""
    for label in [*industry_labels, *instance_labels]:
        v = match_vertical(label)
        if v:
            return v
    return None


def classify_vertical(text: str, *, services: list[str] | None = None, topic: str | None = None) -> str | None:
    """Best-effort vertical from crawled content — homepage text + services + onboarding
    topic. The crawl fallback when a site has no Wikidata entity (no LLM needed)."""
    parts: list[str] = []
    if topic:
        parts.append(topic)
    if services:
        parts.extend(services)
    if text:
        parts.append(text)
    return match_vertical(" \n ".join(parts))


# ── Wikidata (injectable fetch) ─────────────────────────────────────────────────

# Takes a SPARQL query, returns the parsed JSON results (or None on any failure).
SparqlFetch = Callable[[str], Awaitable["dict | None"]]


def _registrable(domain: str) -> str:
    """Bare host without scheme/www (e.g. "https://www.clevelandclinic.org/" →
    "clevelandclinic.org") — the key we match Wikidata's official-website (P856) on."""
    host = host_of(domain) if "://" in domain else host_of("https://" + domain.strip().strip("/"))
    return host.removeprefix("www.")


def _sparql_query(reg_domain: str) -> str:
    # Match the item whose official website (P856) contains this domain, then pull its
    # industry (P452) and instance-of (P31) English labels.
    safe = reg_domain.replace('"', "")
    return (
        "SELECT ?industryLabel ?instanceLabel WHERE { "
        "?item wdt:P856 ?website . "
        f'FILTER(CONTAINS(LCASE(STR(?website)), "{safe}")) . '
        "OPTIONAL { ?item wdt:P452 ?industry . } "
        "OPTIONAL { ?item wdt:P31 ?instance . } "
        'SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . } '
        "} LIMIT 50"
    )


def parse_sparql_industry(data: dict | None) -> str | None:
    """Pull industry/instance labels out of a SPARQL JSON result and map to a vertical."""
    if not isinstance(data, dict):
        return None
    bindings = (((data.get("results") or {}).get("bindings")) or [])
    industry_labels: list[str] = []
    instance_labels: list[str] = []
    for b in bindings:
        if not isinstance(b, dict):
            continue
        ind = (b.get("industryLabel") or {}).get("value")
        ins = (b.get("instanceLabel") or {}).get("value")
        if ind:
            industry_labels.append(str(ind))
        if ins:
            instance_labels.append(str(ins))
    return map_wikidata_labels(industry_labels, instance_labels)


async def _default_sparql_fetch(query: str) -> dict | None:
    import httpx

    # Wikidata's WDQS requires a descriptive User-Agent and rejects generic ones.
    headers = {
        "User-Agent": "AEO-Pipeline/1.0 (industry resolver; contact: ops@aeo.local)",
        "Accept": "application/sparql-results+json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(
                "https://query.wikidata.org/sparql", params={"query": query, "format": "json"}
            )
            if resp.status_code == 200:
                return resp.json()
            log.info("wikidata_sparql_non200", status=resp.status_code)
    except Exception as exc:  # network/parse hiccup → no Wikidata signal, never fatal
        log.warning("wikidata_sparql_failed", error=str(exc))
    return None


async def resolve_wikidata_industry(domain: str, *, fetch: SparqlFetch | None = None) -> str | None:
    """Resolve a domain to a specific industry vertical via Wikidata, or None when there's
    no matching entity / only generic classes. Best-effort and offline-safe (injectable
    ``fetch``); the caller falls back to the crawl classifier and then the coarse label."""
    reg = _registrable(domain)
    if not reg or "." not in reg:
        return None
    fetch = fetch or _default_sparql_fetch
    try:
        data = await fetch(_sparql_query(reg))
    except Exception as exc:
        log.warning("wikidata_resolve_failed", domain=domain, error=str(exc))
        return None
    return parse_sparql_industry(data)
