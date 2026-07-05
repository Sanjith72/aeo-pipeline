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
from ..settings import get_settings
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
# A page is a genuine comparison/alternatives SOURCE only when its own URL PATH says so.
# Scanning every page's <title> for "vs" is exactly what produced phantom competitors:
# a homepage titled "Buy vs Rent Calculator | XYZ Real Estate" is NOT a comparison page,
# so we gate on the URL slug (``/compare``, ``/x-vs-y``, ``/acme-alternative``) and only
# read a title when the URL already proved the page is about a comparison.
_COMPARE_PATH_RE = re.compile(
    r"(?:^|[/\-_])(?:compare|comparison|alternatives?|versus|vs)(?:[/\-_]|$)", re.I
)
# Generic, non-brand words the loose vs/alternative regexes sweep up from marketing copy.
# A real competitor is a proper name — never one of these, and never a phrase made
# entirely of them ("Real Estate", "Buy Calculator").
_COMPETITOR_STOPWORDS = frozenset({
    "buy", "rent", "sell", "lease", "own", "owning", "buying", "renting", "leasing",
    "selling", "real", "estate", "real estate", "calculator", "mortgage", "loan",
    "home", "homes", "house", "houses", "price", "prices", "pricing", "cost", "costs",
    "free", "online", "best", "top", "new", "old", "more", "other", "others", "the",
    "our", "your", "us", "you", "them", "competitor", "competitors", "alternative",
    "alternatives", "comparison", "compare", "review", "reviews", "guide", "vs", "versus",
})

# A section/menu label that GROUPS the specific offerings beneath it ("Our Services" →
# the dropdown items or sub-headings that follow). Matched on the whole (whitespace-
# normalised) label, so it never fires on prose that merely contains the word.
_SERVICE_MENU_RE = re.compile(
    r"^(?:our\s+|the\s+)?(?:services?|products?|solutions?|treatments?|offerings?|"
    r"what\s+we\s+(?:do|offer)|capabilities|expertise|practice\s+areas?|specialt(?:y|ies))$",
    re.I,
)
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# Elements that act as a dropdown/menu trigger (the label sitting above a submenu list).
_MENU_TRIGGER_TAGS = ("a", "button", "span", "summary", "h2", "h3", "h4")

# Nav labels that are never a "service you offer".
_SERVICE_STOPWORDS = frozenset({
    "home", "about", "about us", "contact", "contact us", "blog", "news", "pricing",
    "login", "log in", "sign in", "sign up", "get started", "book now", "careers",
    "privacy", "terms", "faq", "faqs", "resources", "support", "search", "menu",
    "services", "products", "solutions", "our services", "our work", "portfolio",
    "team", "reviews", "testimonials", "gallery", "shop", "cart", "account",
})

# Customer SEGMENTS — who a business SELLS TO, not what it DOES. "Solutions for X" /
# "By industry" / "By team" menus list these (company sizes, served verticals, internal
# departments), and the nav/link scrapers otherwise sweep them into `services`
# (Mailchimp → "Restaurants"/"Small Business"; Slack → "Engineering"/"IT"). Matched on the
# WHOLE normalised label (exact), so multi-word offerings that merely contain one of these
# words survive untouched ("Managed IT", "Content Marketing", "Real Estate Photography").
# Deliberately omits the ambiguous profession words (marketing/legal/finance/sales/design/
# security/consulting) — those are real one-word offerings for agencies/firms, so dropping
# them would erase a marketing agency's "Marketing" or a security firm's "Security".
_SEGMENT_STOPWORDS = frozenset({
    # company-size / buyer tiers
    "small business", "small businesses", "midsize", "mid market", "mid-market", "midmarket",
    "enterprise", "enterprises", "startup", "startups", "smb", "smbs", "freelancer",
    "freelancers", "individuals", "personal", "agencies", "teams", "for teams",
    "for individuals", "for business", "for enterprise",
    # served industries / verticals (the audience a "Solutions for X" menu enumerates)
    "restaurants", "retail", "ecommerce", "e-commerce", "nonprofit", "nonprofits",
    "non-profit", "non-profits", "non profit", "healthcare", "health care", "hospitality",
    "education", "manufacturing", "real estate", "automotive", "government", "public sector",
    "professional services", "financial services", "entertainment", "leisure",
    "entertainment + leisure", "media", "telecom", "telecommunications", "logistics",
    "construction", "insurance", "banking", "travel",
    # company DEPARTMENTS that a "by team" menu lists — rarely a thing the company sells
    "engineering", "it", "information technology", "customer service", "customer support",
    "human resources", "operations", "procurement", "project management",
})

# Navigational call-to-action labels a link scraper mistakes for an offering ("See all
# solutions", "Explore products", "Learn more"). Anchored to a leading CTA verb + an
# all/more tail so a real offering is never caught (e.g. "All-on-4 Dental Implants" stays —
# its "All" is not followed by an all/more token).
_CTA_RE = re.compile(
    r"^(?:see|view|browse|explore|discover)\s+(?:all|our|more)\b"
    r"|^(?:learn|read|show)\s+more\b"
    r"|^get\s+started\b|^request\s+a?\s*demo\b",
    re.I,
)
_MAX_SERVICES = 8
_MAX_COMPETITORS = 6

# Headings that introduce SERVED-vertical copy ("Industries we serve", "Our customers",
# "Case studies", "Solutions for ...") — i.e. the company's *customers'* verticals, not its
# own. Matching industry keywords inside these sections is exactly why a payments company
# read as "Healthcare". The industry classifier reads the site's self-description and skips
# any hero heading that matches this.
_SERVED_VERTICAL_RE = re.compile(
    r"\b(industr(?:y|ies)|who\s+we\s+(?:serve|help|work\s+with)|customers?|clients?|"
    r"use\s+cases?|case\s+stud(?:y|ies)|trusted\s+by|solutions?\s+for|by\s+industry|"
    r"sectors?|verticals?)\b",
    re.I,
)
# How many leading headings count as "the hero" — enough for an H1 + a tagline, not the
# whole page (which would drag body sections back in).
_HERO_HEADING_LIMIT = 3

# CMS fingerprints, checked against the raw page HTML (most-specific first). The detected
# platform drives the "I'll do it myself" instructions — WordPress and Shopify need very
# different paste-the-snippet steps, and "unknown" gets a CMS-agnostic fallback.
CMS_WORDPRESS = "wordpress"
CMS_SHOPIFY = "shopify"
CMS_UNKNOWN = "unknown"
_CMS_FOOTPRINTS: list[tuple[str, re.Pattern[str]]] = [
    (
        CMS_WORDPRESS,
        re.compile(
            r"wp-content|wp-includes"
            r"|<meta[^>]+name=[\"']generator[\"'][^>]+content=[\"']\s*wordpress",
            re.I,
        ),
    ),
    (CMS_SHOPIFY, re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I)),
]


def detect_cms(html_parts: list[str]) -> str:
    """Best-effort CMS detection from raw page HTML — ``'wordpress'`` / ``'shopify'`` /
    ``'unknown'``. Footprint-based (theme asset URLs, generator meta), so it stays cheap and
    offline; an empty/unrecognised site yields ``'unknown'`` rather than guessing."""
    blob = "\n".join(p for p in html_parts if p)
    for name, rx in _CMS_FOOTPRINTS:
        if rx.search(blob):
            return name
    return CMS_UNKNOWN


# Bot-wall / interstitial stub pages: a 200 response that is actually a JS challenge or a
# block notice, not the site. Their tiny body would otherwise become the "offer" ("Client
# Challenge") or feed the industry classifier garbage. We detect the well-known stubs by
# marker text AND small size — a real page that merely mentions "access denied" in copy is
# far larger than a challenge stub, so the size floor keeps it from being dropped.
_CHALLENGE_RE = re.compile(
    r"(?:just a moment|attention required|access denied|client challenge|"
    r"checking your browser|enable javascript to|are you a human|human verification|"
    r"verify you are (?:a )?human|request unsuccessful|error 10\d\d|ddos protection|"
    r"pardon our interruption|cf-browser-verification|"
    r"please (?:enable cookies|complete the security check))",
    re.I,
)
# Outage interstitials ("FedEx | System Down") declare themselves in the TITLE. Matched
# there only — an IT-support site legitimately asks "Is your system down?" in body copy,
# and blanking such a page over prose would erase a real SMB's whole prefill.
_TITLE_OUTAGE_RE = re.compile(r"<title[^>]*>[^<]*\bsystem down\b", re.I)
_CHALLENGE_MAX_BYTES = 20_000


def is_challenge_page(html: str | None) -> bool:
    """True when ``html`` is an obvious bot-wall / interstitial stub (Cloudflare "Just a
    moment", "Client Challenge", "Access Denied", an outage page titled "System Down", …)
    rather than the real site. Empty input counts as unusable."""
    if not html:
        return True
    head = html[:_CHALLENGE_MAX_BYTES]
    return bool(
        _CHALLENGE_RE.search(head) or _TITLE_OUTAGE_RE.search(head)
    ) and len(html) < _CHALLENGE_MAX_BYTES


@dataclass(slots=True)
class SiteFacts:
    location: str | None = None
    services: list[str] = field(default_factory=list)
    competitors: list[dict[str, str]] = field(default_factory=list)
    # Crawl-derived specific vertical (Healthcare, Finance, …) — the fallback when
    # Wikidata has no entity for this site. None when the content gives no clear signal.
    industry: str | None = None
    # Detected publishing platform ('wordpress' | 'shopify' | 'unknown') — drives the
    # CMS-specific copy-paste instructions in the implementation dashboard.
    cms_type: str = CMS_UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "services": self.services,
            "competitors": self.competitors,
            "industry": self.industry,
            "cms_type": self.cms_type,
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


# A JSON-LD field whose "value" is actually a URI/entity reference (sameAs-style
# ``http://www.wikidata.org/entity/Q…`` inside areaServed/address) — never display text.
_URLISH_RE = re.compile(r"^\s*(?:https?://|www\.)", re.I)


def _name_of(value: Any) -> str | None:
    """The ``name`` of a schema node, or the node itself when it's a bare string.
    URL-shaped strings are rejected — an entity URI is a reference, not a name."""
    if isinstance(value, str) and value.strip() and not _URLISH_RE.match(value):
        return value.strip()
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip() and not _URLISH_RE.match(name):
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
    if isinstance(addr, str) and addr.strip() and not _URLISH_RE.match(addr):
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
    low = label.lower()
    if not label or len(label) > 48 or low in _SERVICE_STOPWORDS:
        return None
    # A customer segment (who they serve) or a nav CTA is not an offering.
    if low in _SEGMENT_STOPWORDS or _CTA_RE.search(label):
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


def _menu_label(el: Any) -> str:
    """Whitespace-normalised text of a menu/heading trigger element."""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def services_from_nav(docs: list[FetchedDoc]) -> list[str]:
    """Submenu items grouped under a "Services"/"Products"/"Solutions"/… dropdown.

    Sites that ship no schema.org offerings and no ``/services/<slug>`` URLs still almost
    always expose their specific offerings as a nav dropdown: a trigger label ("Services")
    sitting above a nested ``<ul>`` of links. We collect that list's anchor text."""
    out: list[str] = []
    for doc in docs:
        soup = parse(doc.html)
        for trigger in soup.find_all(_MENU_TRIGGER_TAGS):
            if not _SERVICE_MENU_RE.match(_menu_label(trigger)):
                continue
            # The submenu is the nested list within the trigger's containing <li> (or its
            # immediate parent when the markup isn't list-based).
            container = trigger.find_parent("li") or trigger.parent
            submenu = container.find(["ul", "ol"]) if container is not None else None
            if submenu is None:
                continue
            for a in submenu.find_all("a"):
                cleaned = _clean_service(a.get_text(" ", strip=True))
                if cleaned:
                    out.append(cleaned)
    return out


def services_from_headings(docs: list[FetchedDoc]) -> list[str]:
    """Offering titles listed under a section heading like "Our Services" / "What We Offer".

    Collects the sub-headings nested below such a heading (until the section ends at the
    next same-or-higher-level heading) — the common homepage pattern of an ``<h2>Our
    Services</h2>`` followed by an ``<h3>`` per offering."""
    out: list[str] = []
    for doc in docs:
        soup = parse(doc.html)
        headings = soup.find_all(_HEADING_TAGS)
        for i, h in enumerate(headings):
            if not _SERVICE_MENU_RE.match(_menu_label(h)):
                continue
            level = int(h.name[1])
            for sub in headings[i + 1:]:
                if int(sub.name[1]) <= level:
                    break  # next section at same/higher level closes this one
                cleaned = _clean_service(sub.get_text(" ", strip=True))
                if cleaned:
                    out.append(cleaned)
    return out


# Splits a description into clauses so the offer fallback grabs the first meaningful
# phrase rather than a whole paragraph. Sentence punctuation + spaced dashes / pipes.
_DESC_CLAUSE_SPLIT = re.compile(r"\s*[.;|·••–—\n]\s*|\s+-\s+")
# Trailing function words a mid-sentence cut can leave dangling ("…research group based
# in" → "…research group"). Stripped repeatedly so stacked leftovers all go.
_DANGLING_TAIL_RE = re.compile(
    r"\s+(?:based|in|of|for|and|or|the|a|an|at|with|to|by|from)$", re.I
)
# "Stripe is a financial infrastructure platform…" — a description names its SUBJECT,
# but "what do you offer" wants the predicate. Matches only the copula-with-article form
# ("<Name> is a/an/the …"), so mission-statement prose ("is devoted to…") stays intact.
_SUBJECT_PREFIX_RE = re.compile(r"^[^.;|]{1,60}?\b(?:is|are)\s+(?:a|an|the)\s+", re.I)
_OFFER_CLAUSE_MAX = 60


def first_clause(text: str, *, max_len: int = _OFFER_CLAUSE_MAX) -> str | None:
    """The first meaningful clause of a description-ish string — the shared trimmer
    behind every "what you offer" fallback (crawled meta description, Wikidata's
    one-liner). Cuts at clause punctuation, backs off to a word boundary, and drops
    dangling function words; None when nothing usable remains."""
    if not text:
        return None
    clause = _DESC_CLAUSE_SPLIT.split(text)[0]
    clause = re.sub(r"\s+", " ", clause).strip(" \t\n·|-—–»")
    stripped = _SUBJECT_PREFIX_RE.sub("", clause, count=1)
    if stripped != clause and len(stripped.split()) >= 2:
        clause = stripped
    if len(clause) > max_len:
        head = clause[:max_len]
        # Back off to the previous word boundary so we never end mid-word ("…Chipotle Me").
        if not clause[max_len : max_len + 1].isspace():
            sp = head.rfind(" ")
            if sp > 0:
                head = head[:sp]
        clause = head
        while True:  # a cut can strand function words ("group based in") — drop them all
            trimmed = _DANGLING_TAIL_RE.sub("", clause)
            if trimmed == clause:
                break
            clause = trimmed
    clause = clause.strip(" \t\n·|-—–»")
    # A title like "Home | Jones Day" yields a nav-stopword clause ("Home") — not an offer.
    if not clause or clause.lower() in _SERVICE_STOPWORDS or not re.search(r"[A-Za-z]", clause):
        return None
    return clause


def offer_from_description(soup: Any) -> list[str]:
    """Last-resort "what you offer" label when a site exposes no structured offerings,
    no ``/services`` links, no service nav, and no service-section headings.

    Coarse but honest: the first clause of the site's own meta/OpenGraph description (or
    the ``<title>`` as a final floor), so the wizard's "What do you offer?" is never blank
    after a successful crawl. The user refines a prefill far more readily than they invent
    one from an empty box."""
    text = ""
    for attrs in ({"name": "description"}, {"property": "og:description"}, {"property": "og:title"}):
        meta = soup.find("meta", attrs=attrs)
        content = (meta.get("content") if meta else "") or ""
        if content.strip():
            text = content.strip()
            break
    if not text and soup.title:
        text = soup.title.get_text(" ", strip=True)
    clause = first_clause(text)
    return [clause] if clause else []


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
    name = re.sub(
        r"\b(pricing|review|reviews|comparison|features?|page|calculator|alternatives?)\b.*$",
        "", name, flags=re.I,
    ).strip()
    if not name or len(name) < 2 or len(name) > 40:
        return None
    low = name.lower()
    if low in _COMPETITOR_STOPWORDS:
        return None
    # Reject phrases built entirely from generic words — "Real Estate", "Buy Calculator",
    # "Best Homes" are descriptions, not brands. A real competitor has at least one token
    # that isn't a common marketing/category word.
    if all(word.lower() in _COMPETITOR_STOPWORDS for word in name.split()):
        return None
    return name.title()


def competitors_from_docs(docs: list[FetchedDoc], own: str) -> list[dict[str, str]]:
    """Best-effort, no-LLM: mine the site's own comparison / alternatives pages for the
    OTHER party's name (e.g. ``/compare/usvs-acme``, "Acme alternative", "X vs Y")."""
    own_root = host_of(own).removeprefix("www.").split(".")[0].lower()
    found: list[str] = []
    for doc in docs:
        soup = parse(doc.html)
        haystacks: list[str] = []
        # Only mine THIS page's own url + title when the URL slug proves it's a comparison
        # page. This is the fix for phantom competitors: a homepage whose title merely
        # contains "vs" ("Buy vs Rent Calculator") is no longer treated as a versus page.
        if _COMPARE_PATH_RE.search(doc.url):
            haystacks.append(doc.url)
            if soup.title:
                haystacks.append(soup.title.get_text(" ", strip=True))
        # Anchors are a signal only when the LINK ITSELF points at a comparison/alternatives
        # slug (``/compare/acme``, ``/initech-alternative``) — the rival's name lives in the
        # slug (and, for outbound links, the anchor text). A bare "vs" substring no longer
        # qualifies a link.
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "")
            abs_href = absolute(doc.url, href)
            if not _COMPARE_PATH_RE.search(href) and not _COMPARE_PATH_RE.search(abs_href):
                continue
            haystacks.append(abs_href)
            if not same_site(abs_href, own):  # outbound rival link — its text names them
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


# schema.org ``@type`` → vertical. A site's own structured data DECLARES what the
# business is (a LocalBusiness subtype like Dentist / Restaurant / LawFirm), which is an
# even more direct self-description than title keywords — so it's checked first. Only
# unambiguous business types are mapped; generic containers (Organization, LocalBusiness,
# WebSite, WebPage) carry no vertical and fall through to the keyword classifier.
# Ordered most-specific-first, mirroring _KEYWORD_VERTICALS' controlled vocabulary.
_SCHEMA_TYPE_VERTICALS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("medicalorganization", "hospital", "medicalclinic", "physician", "dentist",
      "medicalbusiness", "pharmacy", "veterinarycare"), "Healthcare"),
    (("restaurant", "fastfoodrestaurant", "cafeorcoffeeshop", "bakery", "barorpub",
      "foodestablishment", "icecreamshop"), "Restaurants"),
    (("legalservice", "attorney", "notary"), "Legal"),
    (("insuranceagency",), "Insurance"),
    (("financialservice", "bankorcreditunion", "accountingservice"), "Finance"),
    (("realestateagent", "realestatelisting"), "Real Estate"),
    (("educationalorganization", "school", "collegeoruniversity", "highschool",
      "elementaryschool", "preschool"), "Education"),
    (("lodgingbusiness", "hotel", "resort", "motel", "bedandbreakfast",
      "travelagency"), "Hospitality / Travel"),
    (("automotivebusiness", "autodealer", "autorepair", "autobodyshop",
      "motorcycledealer", "autopartsstore"), "Automotive"),
    (("exercisegym", "healthclub"), "Fitness"),
    (("beautysalon", "dayspa", "hairsalon", "nailsalon"), "Beauty / Wellness"),
    (("homeandconstructionbusiness", "generalcontractor", "roofingcontractor", "plumber",
      "electrician", "hvacbusiness", "housepainter"), "Construction"),
    (("movingcompany",), "Logistics / Transportation"),
    (("nonprofitorganization", "ngo"), "Nonprofit"),
    (("governmentorganization", "governmentoffice"), "Government"),
    (("newsmediaorganization", "radiostation", "televisionstation"), "Media / Publishing"),
    (("onlinestore",), "E-commerce"),
    (("store", "clothingstore", "shoestore", "furniturestore", "electronicsstore",
      "grocerystore", "jewelrystore", "sportinggoodsstore", "bookstore",
      "departmentstore", "hardwarestore", "petstore", "toystore"), "Retail"),
    # Deliberately NO SoftwareApplication/WebApplication/MobileApplication mapping: any
    # restaurant/bank/gym that promotes its mobile app embeds that type on the homepage, and
    # with schema checked FIRST it would override the true vertical. Real software firms
    # classify fine via the title/self-description keywords ("software", "saas", …).
)


def vertical_from_schema_types(blocks: list[dict]) -> str | None:
    """Vertical from the page's own schema.org ``@type`` declarations, or None when only
    generic/unmapped types are present. First mapped type wins (most-specific-first
    order, same contract as ``match_vertical``)."""
    types: set[str] = set()
    for obj in blocks:
        if isinstance(obj, dict):
            types |= _type_set(obj)
    for keys, vertical in _SCHEMA_TYPE_VERTICALS:
        if types.intersection(keys):
            return vertical
    return None


def self_description_text(soup: Any) -> str:
    """The site's OWN self-description — ``<title>``, the meta description, OpenGraph
    title/description/site-name, and the hero heading(s) — and deliberately NOT the page
    body.

    Industry is a PROVENANCE problem, not a confidence one: the body is where
    "Industries we serve / Our customers / Case studies" copy lives, so matching vertical
    keywords there misreads a payments company as "Healthcare" — a *strong* match on the
    wrong text, which no confidence threshold would catch. Self-description text describes
    the company itself, so it's the honest signal for the company's own vertical; a hero
    heading that introduces a served-vertical section is skipped for the same reason."""
    parts: list[str] = []
    if soup.title:
        parts.append(soup.title.get_text(" ", strip=True))
    for attrs in (
        {"name": "description"},
        {"property": "og:title"},
        {"property": "og:description"},
        {"property": "og:site_name"},
    ):
        meta = soup.find("meta", attrs=attrs)
        content = (meta.get("content") if meta else "") or ""
        if content.strip():
            parts.append(content.strip())
    for h in soup.find_all(("h1", "h2"), limit=_HERO_HEADING_LIMIT):
        label = re.sub(r"\s+", " ", h.get_text(" ", strip=True)).strip()
        if label and not _SERVED_VERTICAL_RE.search(label):
            parts.append(label)
    return " \n ".join(p for p in parts if p)


def extract_facts(docs: list[FetchedDoc], *, domain: str) -> SiteFacts:
    """Fold the fetched pages into a :class:`SiteFacts` (pure; no network)."""
    own = normalize(docs[0].url) if docs else f"https://{domain}/"
    blocks: list[dict] = []
    home_blocks: list[dict] = []  # homepage-only JSON-LD — the industry signal (see below)
    text_parts: list[str] = []
    internal_urls: list[str] = []
    for i, doc in enumerate(docs):
        soup = parse(doc.html)
        sj = schema_jsonld.extract(doc.html, soup, doc.url)
        blocks.extend(sj.get("blocks", []))
        if i == 0:
            home_blocks = list(sj.get("blocks", []))
        text_parts.append(soup.get_text(" ", strip=True))
        lk = links_extractor.extract(doc.html, soup, doc.url)
        internal_urls.extend(lk.get("internal", []))

    location = (
        location_from_blocks(blocks)
        or location_from_text(" ".join(text_parts))
        or infer_location(to_page_views(_url_views(internal_urls + [d.url for d in docs])))
    )
    # Highest precision first (schema.org → dedicated service-page links), then the
    # higher-recall fallbacks (nav dropdowns, section headings) that catch sites which
    # list offerings inline. Dedupe keeps the earliest, best-sourced label.
    services = _dedupe_keep_order(
        [s for s in (_clean_service(x) for x in services_from_blocks(blocks)) if s]
        + services_from_links(docs, own)
        + services_from_nav(docs)
        + services_from_headings(docs),
        _MAX_SERVICES,
    )
    competitors = competitors_from_docs(docs, own)
    # Classify the company's OWN vertical from the HOMEPAGE self-description (title/meta/hero)
    # ONLY — not the body, not the scraped services, and not key pages. Each of those carries
    # served-vertical contamination: the body has "industries we serve" copy, services can be
    # customer-segment links, and key pages (/solutions/restaurants, a wellness template
    # gallery) are vertical-specific and name-drop verticals in their own titles. The
    # homepage is where a company states what it IS. Abstain (None) when it's generic —
    # empty beats confidently wrong (a marketing platform read as "Restaurants").
    home_soup = parse(docs[0].html) if docs else None
    # Schema-first: the homepage's own JSON-LD ``@type`` (Dentist, Restaurant, LawFirm…)
    # is the business declaring what it IS — stronger than any keyword heuristic, so it
    # wins outright. Homepage blocks ONLY: key pages can carry vertical-specific types
    # about content rather than the company (a /solutions/restaurants page, a listing).
    # Title-second: the <title> is the most authoritative textual self-description, so
    # classify it alone before widening — a promo heading ("HIGH SCHOOL SUMMER PASS")
    # then can't override the lead ("Planet Fitness | A Gym and Fitness Club"). Only when
    # the title names no vertical do we widen to the fuller self-description (meta/og/hero).
    title_desc = (home_soup.title.get_text(" ", strip=True) if home_soup and home_soup.title else "")
    industry = (
        vertical_from_schema_types(home_blocks)
        or classify_vertical(title_desc)
        or classify_vertical(self_description_text(home_soup) if home_soup is not None else "")
    )
    # NOTE: industry is classified from the homepage self-description ONLY — NOT from the
    # scraped service labels. A services-based recovery used to run here, but service labels
    # are frequently use-cases / served-segments, so keyword-matching them produced confident
    # WRONG verticals ("AI app builder" → Construction; a "Marketing" team segment →
    # Marketing/Advertising; "Marketing Sites" → Marketing). Empty beats confidently wrong,
    # so we abstain (None) instead — the user fills a blank far more readily than they catch a
    # wrong guess. (The classify_vertical(services=) capability stays for callers that pass a
    # trusted, curated services list, e.g. an onboarding brief.)
    # "What do you offer?" must never come back empty from a crawl that found a page.
    # Tiered fallback: structured offerings (above) → first clause of the site's own
    # description → the classified industry label. The user edits whichever lands.
    if not services and docs:
        services = offer_from_description(parse(docs[0].html))
    if not services and industry:
        services = [industry]
    cms_type = detect_cms([d.html for d in docs])
    return SiteFacts(
        location=location, services=services, competitors=competitors,
        industry=industry, cms_type=cms_type,
    )


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
    wizard prefill never blocks the flow.

    Defaults to a browser-headers fetch (``browser_fetch_text``): this path reads arbitrary
    third-party marketing sites for the wizard prefill, and many of those serve a 403 / JS
    bot-challenge stub to the identified AEOBot UA while returning real HTML to a browser.
    The audit crawler keeps the polite AEOBot UA via its own ``_default_fetch_text``.

    When the cheap fetch is blocked AND no custom ``fetch`` was injected, the homepage is
    retried once with a real headless Chromium (``playwright_fetch_text``) — gated by
    ``intake.playwright_fallback``. That render (~6-8s) recovers sites behind walls a bare
    HTTP client can't pass (planetfitness/warbyparker/allbirds); it fires only on the
    minority of blocked sites, so the common case never pays the cost."""
    from ..crawl.discovery import browser_fetch_text, playwright_fetch_text, seed_url

    injected = fetch is not None
    fetch = fetch or browser_fetch_text
    home = seed_url(domain)
    try:
        home_html = await fetch(home)
    except Exception as exc:  # network hiccup → empty facts, never fatal
        log.warning("site_facts_homepage_failed", domain=domain, error=str(exc))
        home_html = None
    # A blank homepage OR a bot-wall interstitial (200 challenge stub) → no usable content.
    # Last resort on the real path: render the homepage with a headless browser, which gets
    # past walls the HTTP client can't. Only the homepage — industry/offer live there, and a
    # per-key-page render would blow the latency budget.
    if is_challenge_page(home_html) and not injected and get_settings().intake.playwright_fallback:
        rendered = await playwright_fetch_text(home)
        if not is_challenge_page(rendered):
            home_html = rendered
    # Classifying/offer-extracting from a "Client Challenge" stub yields garbage, so abstain.
    if is_challenge_page(home_html):
        return SiteFacts()

    docs = [FetchedDoc(url=home, html=home_html)]
    for url in _select_key_pages(home, home_html, limit=max(0, max_pages - 1)):
        try:
            html = await fetch(url)
        except Exception:
            html = None
        if html and not is_challenge_page(html):
            docs.append(FetchedDoc(url=url, html=html))

    try:
        return extract_facts(docs, domain=domain)
    except Exception as exc:  # extraction must never break intake
        log.warning("site_facts_extract_failed", domain=domain, error=str(exc))
        return SiteFacts()
