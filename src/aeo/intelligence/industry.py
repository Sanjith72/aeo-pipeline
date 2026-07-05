"""
Industry resolution — map a site to a *specific* vertical (Healthcare, Finance,
Restaurants, …) instead of the generic coarse label.

Two sources, tried in order by the caller:

  1. **Wikidata** (:func:`resolve_wikidata_profile`) — the deterministic, no-LLM path.
     Resolve the site by its official website (P856) to a Wikidata item, read the
     ``industry`` (P452) and ``instance of`` (P31) labels, drop generic classes
     ("business enterprise", "company", …), and map what's left to a vertical.
     The default retrieval is the **entity-API fast path** (an exact ``haswbstatement``
     P856 search + ``wbgetentities``, measured ~0.4–2s end to end); the WDQS SPARQL
     scan (~38–50s uncontended — the reason enrichment was once gated off) remains only
     behind the injectable ``fetch`` seam.
  2. **Crawl text** (:func:`classify_vertical`) — a keyword classifier over the crawled
     homepage text / services / topic, for sites with no Wikidata entity.

Both funnel through one ordered keyword map (:func:`match_vertical`) so a raw label and a
chunk of page text resolve to the same controlled vocabulary. Pure + offline-testable:
both resolvers take an injectable fetch.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

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
    # Insurance stays above Real Estate: title insurers self-describe with real-estate
    # tokens ("title insurance for real estate closings") but ARE insurance.
    (("insurance", "insurer"), "Insurance"),
    # Real Estate precedes Finance: a listings portal legitimately says both "real estate"
    # and "mortgages" (zillow), and with Finance first the mortgage keyword mislabeled it.
    # A business that self-describes with an explicit real-estate token IS one; a pure
    # lender says "mortgage" without it and still lands on Finance below.
    (("real estate", "realty", "realtor", "property management", "property developer"), "Real Estate"),
    (("bank", "banking", "financial service", "finance", "investment", "asset management",
      "fintech", "credit union", "lending", "mortgage", "wealth management", "brokerage"), "Finance"),
    (("restaurant", "fast food", "cafe", "café", "catering", "food service", "diner", "bistro",
      "taqueria", "pizzeria", "steakhouse", "eatery", "food truck", "coffeehouse", "coffee shop"), "Restaurants"),
    (("e-commerce", "ecommerce", "online store", "online shop", "marketplace", "online retail"), "E-commerce"),
    (("retail", "supermarket", "grocery", "department store", "retailer"), "Retail"),
    (("law firm", "legal service", "attorney", "lawyer", "litigation", "law practice"), "Legal"),
    (("education", "school", "university", "college", "academy", "e-learning", "edtech", "tutoring"), "Education"),
    # "managed it!" is exact-ended (see the "!" marker in the pattern builder): it matches
    # "Managed IT Services" / "Managed IT for business" but not "managed itineraries".
    (("software", "saas", "information technology", "cloud computing", "internet company",
      "technology company", "computer software", "web development", "it service",
      "managed it!"), "Software / SaaS"),
    (("manufactur", "industrial", "factory", "fabrication"), "Manufacturing"),
    # "builder" alone is too broad — it mislabels "website/app/page/form builder" SaaS as
    # Construction (squarespace, figma). Require a construction-specific form instead (these
    # still cover the trades: home/house/pool/deck/fence builder, general contractor).
    (("construction", "contractor", "home builder", "homebuilder", "house builder",
      "pool builder", "deck builder", "fence builder", "custom home",
      "roofing", "plumbing", "hvac", "remodeling", "general contractor"), "Construction"),
    (("hotel", "hospitality", "tourism", "travel agency", "airline", "resort", "lodging"), "Hospitality / Travel"),
    (("marketing", "advertising", "public relations", "seo agency", "digital agency", "branding"), "Marketing / Advertising"),
    (("media", "publishing", "newspaper", "broadcast", "magazine", "entertainment", "film", "music"), "Media / Publishing"),
    (("automotive", "automobile", "car dealer", "vehicle", "auto repair", "suv",
      "minivan"), "Automotive"),
    # "donation" (the noun — deliberately NOT the CTA verb "donate", which leaks into
    # og/meta on quasi-commercial campaign sites) and "humanitarian" cover relief orgs
    # whose self-description never says "nonprofit" outright (redcross).
    (("non-profit", "nonprofit", "not-for-profit", "charity", "charitable", "ngo",
      "foundation", "donation", "humanitarian"), "Nonprofit"),
    (("energy", "oil and gas", "petroleum", "utility", "renewable", "solar", "electric utility"), "Energy"),
    (("telecommunication", "telecom", "wireless carrier", "mobile network"), "Telecommunications"),
    # "shipping" alone matches the ubiquitous e-commerce phrase "free shipping" — require a
    # freight/carrier context so a shoe shop isn't read as a logistics company.
    (("logistics", "transportation", "freight", "courier", "delivery service", "trucking",
      "freight shipping", "shipping company", "shipping line", "container shipping"), "Logistics / Transportation"),
    (("agricultur", "farming", "farm"), "Agriculture"),
    (("beauty", "cosmetic", "salon", "spa", "skincare", "wellness"), "Beauty / Wellness"),
    (("fitness", "gym", "personal training", "yoga studio"), "Fitness"),
    (("food", "beverage", "brewery", "winery", "distillery", "bakery"), "Food & Beverage"),
    (("consulting", "consultancy", "professional service", "advisory"), "Professional services"),
    (("government", "public sector", "municipal"), "Government"),
)


# Each keyword must begin at a WORD BOUNDARY — otherwise naive substring matching fires on
# mid-word coincidences ("spa" inside "workspace" → Beauty/Wellness; "it" inside "submit").
# The boundary is only at the START, so the intentional stems still match as prefixes
# ("cybersecur" → "cybersecurity", "manufactur" → "manufacturing"). A keyword ending in
# "!" opts into a TRAILING boundary as well — for phrases whose prefix form collides with
# unrelated words ("managed it!" must not match "managed itineraries").
def _keyword_pattern(keyword: str) -> str:
    if keyword.endswith("!"):
        return re.escape(keyword[:-1]) + r"\b"
    return re.escape(keyword)


_VERTICAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(r"\b(?:" + "|".join(_keyword_pattern(k) for k in keywords) + r")"), vertical)
    for keywords, vertical in _KEYWORD_VERTICALS
)


def match_vertical(text: str) -> str | None:
    """Map a raw label / chunk of text to a specific vertical, or None. Generic-only
    text (e.g. "business enterprise") yields None so the caller falls through. Keywords
    match only at a word boundary, so "workspace" is not Beauty/Wellness. Order matters
    (most-specific-first): a "retail bank" resolves to Finance, not Retail."""
    if not text:
        return None
    low = text.lower()
    if low.strip() in GENERIC_LABELS:
        return None
    for pattern, vertical in _VERTICAL_PATTERNS:
        if pattern.search(low):
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


def _host_regex(reg_domain: str) -> str:
    """A SPARQL-literal regex that matches a P856 official-website value ONLY when it is the
    registrable domain's own root — ``^https?://(www.)?<host>/?$`` — case-insensitive.

    This is deliberately anchored, not a substring ``CONTAINS``: matching the bare host as a
    substring pulls in subsidiary URLs, longer paths, and coincidental matches (e.g.
    ``ibm.com`` matched an unrelated SF "productivity software" item), and the parser then
    faithfully returns the WRONG company's HQ/products. A wrong prefill is worse than none in
    onboarding, since ``location`` flows into competitor discovery. Anchoring rejects all of
    those in one move (and narrows what the engine scans).

    Dots are escaped for the regex AND doubled for the SPARQL string literal (so SPARQL
    unescapes ``\\\\.`` → ``\\.`` → a literal dot). The host is sanitised first so it can't
    break out of the literal or inject regex metacharacters."""
    host = reg_domain.replace("\\", "").replace('"', "")
    host_pat = host.replace(".", r"\\.")  # each "." → SPARQL-literal \\.  → regex \.
    return rf"^https?://(www\\.)?{host_pat}/?$"


def _sparql_query(reg_domain: str) -> str:
    # Match the item whose official website (P856) IS this exact registrable domain (anchored
    # host regex — see _host_regex), then pull its industry (P452) and instance-of (P31)
    # English labels plus the richer "About you" signals: headquarters (P159) and its country
    # (P17), products/materials produced (P1056), and the item's English schema:description
    # one-liner. The label SERVICE auto-derives ?<var>Label for every selected entity
    # variable; ?description is a plain literal (a language-tagged string), filtered to English.
    pattern = _host_regex(reg_domain)
    # Several entities can legitimately share one official website (a global parent plus its
    # national subsidiaries — e.g. IBM and IBM Deutschland both point at ibm.com). Pick the
    # SINGLE most-notable match (most Wikipedia sitelinks) in an inner subquery, so HQ /
    # products / description come from the parent company, not whichever subsidiary sorted
    # first. With a lone small-business entity the ORDER BY/LIMIT 1 simply returns it.
    return (
        "SELECT ?industryLabel ?instanceLabel ?hqLabel ?countryLabel ?productLabel "
        "?description WHERE { "
        "{ SELECT ?item WHERE { "
        "?item wdt:P856 ?website . "
        f'FILTER(REGEX(STR(?website), "{pattern}", "i")) . '
        "?item wikibase:sitelinks ?sl . "
        "} ORDER BY DESC(?sl) LIMIT 1 } "
        "OPTIONAL { ?item wdt:P452 ?industry . } "
        "OPTIONAL { ?item wdt:P31 ?instance . } "
        "OPTIONAL { ?item wdt:P159 ?hq . OPTIONAL { ?hq wdt:P17 ?country . } } "
        "OPTIONAL { ?item wdt:P1056 ?product . } "
        'OPTIONAL { ?item schema:description ?description . FILTER(LANG(?description) = "en") } '
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


@dataclass(slots=True)
class WikidataProfile:
    """Best-effort "About you" facts resolved from a site's Wikidata item — every field
    is optional (None / empty) when Wikidata has no value for it. Mirrors what the wizard
    prefills: a specific industry vertical, a headquarters location + its country, the
    products/services produced (the "what you offer" signal), and a one-line description."""

    industry: str | None = None
    location: str | None = None
    country: str | None = None
    offerings: list[str] = field(default_factory=list)
    description: str | None = None

    def has_signal(self) -> bool:
        """True when at least one field carries a value — drives cache TTL (a real entity
        with facts is cached long, an empty/no-entity result re-checks sooner)."""
        return bool(
            self.industry or self.location or self.country or self.offerings or self.description
        )


_MAX_OFFERINGS = 8


def _dedupe_keep_order(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if not it or key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def parse_sparql_profile(data: dict | None) -> WikidataProfile:
    """Fold a SPARQL JSON result into a :class:`WikidataProfile`. Reuses the P452/P31 →
    vertical mapping for ``industry`` (generic-only labels still fall through to None) and
    pulls the first HQ/country/description plus all distinct products produced. An empty /
    non-dict result yields an all-empty profile, never raises."""
    profile = WikidataProfile()
    if not isinstance(data, dict):
        return profile
    bindings = (((data.get("results") or {}).get("bindings")) or [])
    industry_labels: list[str] = []
    instance_labels: list[str] = []
    offerings: list[str] = []
    for b in bindings:
        if not isinstance(b, dict):
            continue
        ind = (b.get("industryLabel") or {}).get("value")
        ins = (b.get("instanceLabel") or {}).get("value")
        if ind:
            industry_labels.append(str(ind))
        if ins:
            instance_labels.append(str(ins))
        if profile.location is None:
            hq = (b.get("hqLabel") or {}).get("value")
            if hq:
                profile.location = str(hq).strip() or None
        if profile.country is None:
            country = (b.get("countryLabel") or {}).get("value")
            if country:
                profile.country = str(country).strip() or None
        if profile.description is None:
            desc = (b.get("description") or {}).get("value")
            if desc:
                profile.description = str(desc).strip() or None
        product = (b.get("productLabel") or {}).get("value")
        if product and str(product).strip():
            offerings.append(str(product).strip())
    profile.industry = map_wikidata_labels(industry_labels, instance_labels)
    profile.offerings = _dedupe_keep_order(offerings, _MAX_OFFERINGS)
    return profile


# WDQS is genuinely slow for these scans (measured ~1–15s, occasionally 30s+). The lookup
# runs CONCURRENTLY with the homepage crawl in /api/profile (itself ~10s+), so a timeout in
# that range is nearly free — it rarely extends the total wait beyond the crawl — while a
# tighter 10s cap lost too many otherwise-valid responses.
_SPARQL_TIMEOUT = 12.0


async def _default_sparql_fetch(query: str) -> dict | None:
    import httpx

    from ..crawl.transport import async_transport

    # Wikidata's WDQS requires a descriptive User-Agent and rejects generic ones.
    headers = {
        "User-Agent": "AEO-Pipeline/1.0 (industry resolver; contact: ops@aeo.local)",
        "Accept": "application/sparql-results+json",
    }
    # Route through the force-IPv4 transport seam like every other outbound client: on a
    # dual-stack host that stalls on AAAA (OCI Ampere, and observed locally), the WDQS
    # request otherwise hangs ~20s+ and trips the timeout, silently emptying every profile.
    try:
        async with httpx.AsyncClient(
            timeout=_SPARQL_TIMEOUT, follow_redirects=True, headers=headers,
            transport=async_transport(),
        ) as client:
            resp = await client.get(
                "https://query.wikidata.org/sparql", params={"query": query, "format": "json"}
            )
            if resp.status_code == 200:
                return resp.json()
            log.info("wikidata_sparql_non200", status=resp.status_code)
    except Exception as exc:  # network/parse hiccup → no Wikidata signal, never fatal
        log.warning("wikidata_sparql_failed", error=str(exc))
    return None


# ── entity-API fast path (the default network retrieval) ─────────────────────────

_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
# Small keyed lookups (statement search + entity fetch), measured ~0.3–0.6s each; a tight
# per-call timeout keeps the 3-call worst case inside the /api/profile budget.
_API_TIMEOUT = 6.0
_MAX_ENTITY_CANDIDATES = 5

# Takes wikidata API params, returns the parsed JSON (or None on any failure).
ApiFetch = Callable[[dict], Awaitable["dict | None"]]


def _p856_variants(reg_domain: str) -> list[str]:
    """The exact P856 values a site is realistically recorded under — https/http ×
    www/bare × trailing-slash/none (Mayo Clinic alone carries two different forms).
    Exact-statement matching replaces the WDQS anchored-regex scan: same precision
    (never a substring match), a fraction of the cost."""
    return [
        f"{scheme}://{www}{reg_domain}{tail}"
        for scheme in ("https", "http")
        for www in ("www.", "")
        for tail in ("", "/")
    ]


def _statement_search_query(reg_domain: str) -> str:
    """CirrusSearch query matching an item whose official website (P856) IS one of the
    domain's exact variant forms (``haswbstatement`` supports ``|``-OR'd statements)."""
    return "haswbstatement:" + "|".join(f"P856={v}" for v in _p856_variants(reg_domain))


async def _default_api_fetch(params: dict) -> dict | None:
    import httpx

    from ..crawl.transport import async_transport

    # Wikimedia APIs want a descriptive User-Agent; force-IPv4 seam like every other client.
    headers = {"User-Agent": "AEO-Pipeline/1.0 (industry resolver; contact: ops@aeo.local)"}
    try:
        async with httpx.AsyncClient(
            timeout=_API_TIMEOUT, headers=headers, transport=async_transport()
        ) as client:
            resp = await client.get(_WIKIDATA_API, params={**params, "format": "json"})
            if resp.status_code == 200:
                return resp.json()
            log.info("wikidata_api_non200", status=resp.status_code)
    except Exception as exc:  # network hiccup → no Wikidata signal, never fatal
        log.warning("wikidata_api_failed", error=str(exc))
    return None


def _claim_qids(claims: dict, prop: str) -> list[str]:
    """The value-entity QIDs of a property's claims (skips novalue/somevalue snaks)."""
    out: list[str] = []
    for c in claims.get(prop) or []:
        value = (((c or {}).get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            out.append(str(value["id"]))
    return out


async def _profile_via_entity_api(
    reg_domain: str, api_fetch: ApiFetch
) -> tuple[WikidataProfile | None, bool]:
    """Resolve the domain's :class:`WikidataProfile` through the entity API.

    Three keyed calls: exact-P856 statement search → ``wbgetentities`` for the item's
    claims/description/sitelinks → one labels batch for the claim-value QIDs. Returns
    ``(profile, cacheable)``: profile is ``None`` on a transient failure of the first
    two calls (the caller must NOT cache that as a miss) and an all-empty profile when
    the domain genuinely has no entity. A failed labels batch degrades to the
    description-only signals rather than failing — but flags the result NON-cacheable,
    so one Wikimedia hiccup can't pin a location-less profile for the 7-day hit TTL."""
    search = await api_fetch({
        "action": "query", "list": "search",
        "srsearch": _statement_search_query(reg_domain), "srlimit": _MAX_ENTITY_CANDIDATES,
    })
    # MediaWiki reports failures as HTTP-200 {"error": {...}} payloads — transient like a
    # network failure (retry next time), never a genuine no-entity miss.
    if search is None or "error" in search:
        return None, False
    qids = [
        str(hit.get("title"))
        for hit in ((search.get("query") or {}).get("search") or [])
        if isinstance(hit, dict) and re.fullmatch(r"Q\d+", str(hit.get("title") or ""))
    ]
    if not qids:
        return WikidataProfile(), True

    ents = await api_fetch({
        "action": "wbgetentities", "ids": "|".join(qids),
        "props": "claims|descriptions|sitelinks|labels", "languages": "en",
    })
    if ents is None or "error" in ents:
        return None, False
    entities = [
        e for e in ((ents.get("entities") or {}).values())
        if isinstance(e, dict) and isinstance(e.get("claims"), dict)
    ]
    if not entities:
        return WikidataProfile(), True
    # Several entities can share one official website (a global parent plus national
    # subsidiaries) — most Wikipedia sitelinks = the notable parent, same rule the old
    # SPARQL subquery used.
    entity = max(entities, key=lambda e: len(e.get("sitelinks") or {}))
    claims = entity["claims"]
    industry_qids = _claim_qids(claims, "P452")
    instance_qids = _claim_qids(claims, "P31")
    hq_qids = _claim_qids(claims, "P159")[:1]
    product_qids = _claim_qids(claims, "P1056")[:_MAX_OFFERINGS]
    description = (
        str((((entity.get("descriptions") or {}).get("en")) or {}).get("value") or "").strip()
        or None
    )

    label_of: dict[str, str] = {}
    cacheable = True
    value_qids = _dedupe_keep_order(
        [*industry_qids, *instance_qids, *hq_qids, *product_qids], 40
    )
    if value_qids:
        labels = await api_fetch({
            "action": "wbgetentities", "ids": "|".join(value_qids),
            "props": "labels", "languages": "en",
        })
        # A failed batch means the profile below is missing its location/offerings —
        # usable for this one response, but not a real answer to pin in the cache.
        cacheable = labels is not None and "error" not in labels
        for qid, ent in (((labels or {}).get("entities")) or {}).items():
            lab = str(((((ent or {}).get("labels") or {}).get("en")) or {}).get("value") or "").strip()
            if lab:
                label_of[str(qid)] = lab

    profile = WikidataProfile(description=description)
    # P452 first (the truest industry signal), then P31 classes; when both are generic
    # the entity's own one-line description is the tiebreak — it names the vertical in
    # plain words ("medical practice and medical research group…"), which is exactly
    # what match_vertical reads.
    profile.industry = map_wikidata_labels(
        [label_of[q] for q in industry_qids if q in label_of],
        [label_of[q] for q in instance_qids if q in label_of],
    ) or match_vertical(description or "")
    profile.location = label_of.get(hq_qids[0]) if hq_qids else None
    # Drop a P1056 offering that merely echoes the brand itself — Nike's "products
    # produced" is the item "Nike", which answers "what do you offer?" with the
    # company name. An echo is no offering; better to fall through to the description.
    entity_label = (
        str((((entity.get("labels") or {}).get("en")) or {}).get("value") or "")
        .strip()
        .lower()
    )
    profile.offerings = _dedupe_keep_order(
        [
            label_of[q] for q in product_qids
            if q in label_of and label_of[q].strip().lower() != entity_label
        ],
        _MAX_OFFERINGS,
    )
    return profile, cacheable


# ── response cache ───────────────────────────────────────────────────────────────
# Wikidata changes rarely and WDQS rate-limits, so cache the resolved vertical per
# registrable domain in-process (consistent with the app's other in-memory registries).
# Hits live long; misses re-check sooner in case an entity gets added later. Only real
# SPARQL responses are cached — transient network/non-200 failures are not, so they retry.
_CACHE: dict[str, tuple[float, str | None]] = {}
# A parallel cache for the richer profile lookups (same registrable-domain key, same TTL
# policy), so resolve_wikidata_profile is cached independently of resolve_wikidata_industry.
_PROFILE_CACHE: dict[str, tuple[float, WikidataProfile | None]] = {}
_CACHE_TTL_HIT = 7 * 24 * 3600   # 7 days
_CACHE_TTL_MISS = 24 * 3600      # 1 day
_CACHE_MAX = 4096


def clear_wikidata_cache() -> None:
    """Drop all cached Wikidata lookups (used by tests; safe to call anytime)."""
    _CACHE.clear()
    _PROFILE_CACHE.clear()


def _cache_get(cache: dict, key: str) -> tuple[bool, Any]:
    """(hit, value) — ``hit`` is False on a miss or an expired entry."""
    entry = cache.get(key)
    if entry is None or entry[0] <= time.monotonic():
        return False, None
    return True, entry[1]


def _cache_put(cache: dict, key: str, value: Any, *, hit: bool) -> None:
    """Store ``value`` under ``key``. A real-data ``hit`` lives long; a miss (None / no
    entity) re-checks sooner in case an entity gets added to Wikidata later."""
    if len(cache) >= _CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, (exp, _) in cache.items() if exp <= now]:
            del cache[k]
        if len(cache) >= _CACHE_MAX:  # still full of live entries → reset (internal-tool scale)
            cache.clear()
    ttl = _CACHE_TTL_HIT if hit else _CACHE_TTL_MISS
    cache[key] = (time.monotonic() + ttl, value)


async def resolve_wikidata_industry(
    domain: str, *, fetch: SparqlFetch | None = None, use_cache: bool = True
) -> str | None:
    """Resolve a domain to a specific industry vertical via Wikidata, or None when there's
    no matching entity / only generic classes. Best-effort and offline-safe (injectable
    ``fetch``); the caller falls back to the crawl classifier and then the coarse label.

    Successful lookups are cached per registrable domain (see the cache constants above).
    The cache is bypassed when a custom ``fetch`` is injected (tests/custom callers) or
    when ``use_cache=False``."""
    reg = _registrable(domain)
    if not reg or "." not in reg:
        return None

    cache_on = use_cache and fetch is None
    if cache_on:
        hit, value = _cache_get(_CACHE, reg)
        if hit:
            return value

    fetch = fetch or _default_sparql_fetch
    try:
        data = await fetch(_sparql_query(reg))
    except Exception as exc:
        log.warning("wikidata_resolve_failed", domain=domain, error=str(exc))
        return None

    vertical = parse_sparql_industry(data)
    # Cache only a real response — a None ``data`` means a network/non-200 hiccup, which
    # should retry rather than stick as a cached miss.
    if cache_on and data is not None:
        _cache_put(_CACHE, reg, vertical, hit=vertical is not None)
    return vertical


async def resolve_wikidata_profile(
    domain: str,
    *,
    fetch: SparqlFetch | None = None,
    api_fetch: ApiFetch | None = None,
    use_cache: bool = True,
) -> WikidataProfile:
    """Resolve a domain's richer "About you" facts via Wikidata — a specific industry
    vertical plus headquarters location/country, products produced, and a one-line
    description. Returns an all-empty :class:`WikidataProfile` (never raises) when there's
    no matching entity or on any network/parse failure; the caller merges these
    best-source-first behind the crawl signals.

    Retrieval is the entity-API fast path (see :func:`_profile_via_entity_api`) — quick
    enough for the synchronous ``/api/profile`` budget. A caller that injects the legacy
    SPARQL ``fetch`` gets the WDQS path instead; ``api_fetch`` injects the fast path's
    HTTP seam for tests. The in-process cache (same TTL policy as
    :func:`resolve_wikidata_industry`, separate store) is bypassed when either fetch is
    injected or ``use_cache=False``."""
    reg = _registrable(domain)
    if not reg or "." not in reg:
        return WikidataProfile()

    cache_on = use_cache and fetch is None and api_fetch is None
    if cache_on:
        hit, value = _cache_get(_PROFILE_CACHE, reg)
        if hit:
            return value if value is not None else WikidataProfile()

    if fetch is not None:
        # Legacy WDQS/SPARQL seam — kept for callers/tests that inject it.
        try:
            data = await fetch(_sparql_query(reg))
        except Exception as exc:
            log.warning("wikidata_profile_resolve_failed", domain=domain, error=str(exc))
            return WikidataProfile()
        return parse_sparql_profile(data) if data is not None else WikidataProfile()

    try:
        profile, cacheable = await _profile_via_entity_api(reg, api_fetch or _default_api_fetch)
    except Exception as exc:
        log.warning("wikidata_profile_resolve_failed", domain=domain, error=str(exc))
        return WikidataProfile()
    # A transient failure (None) must retry next time — only a real answer is cacheable,
    # whether that answer carries signal (long TTL) or is a genuine no-entity miss. A
    # labels-degraded profile (cacheable=False) is served once but never pinned.
    if profile is None:
        return WikidataProfile()
    if cache_on and cacheable:
        _cache_put(_PROFILE_CACHE, reg, profile, hit=profile.has_signal())
    return profile
