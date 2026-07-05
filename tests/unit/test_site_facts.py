"""
Crawl-derived intake facts — Location, what-you-offer (services), and best-effort
on-site competitors. Pure + offline: extraction runs on inline HTML; the network
gatherer takes an injectable fetch.
"""

from __future__ import annotations

import asyncio

from aeo.intelligence.site_facts import (
    FetchedDoc,
    competitors_from_docs,
    detect_cms,
    extract_facts,
    gather_site_facts,
    is_challenge_page,
    location_from_blocks,
    location_from_text,
    offer_from_description,
    services_from_blocks,
    services_from_headings,
    services_from_links,
    services_from_nav,
)

_LD_LOCAL = """
<html><head>
<script type="application/ld+json">
{"@type":"Dentist","name":"Harbor Dental",
 "address":{"@type":"PostalAddress","addressLocality":"Austin","addressRegion":"TX"},
 "hasOfferCatalog":{"@type":"OfferCatalog","itemListElement":[
   {"@type":"Offer","itemOffered":{"@type":"Service","name":"Teeth Whitening"}},
   {"@type":"Offer","itemOffered":{"@type":"Service","name":"Dental Implants"}}]}}
</script></head>
<body>
<nav><a href="/services/teeth-whitening">Teeth Whitening</a>
     <a href="/services/checkups">Routine Checkups</a>
     <a href="/about">About</a></nav>
<footer>Visit us — Austin, TX 78701</footer>
</body></html>
"""


def test_location_from_jsonld_address():
    facts = extract_facts([FetchedDoc("https://harbor.com/", _LD_LOCAL)], domain="harbor.com")
    assert facts.location == "Austin, TX"


def test_location_from_text_fallback():
    assert location_from_text("Come by our shop at Austin, TX 78701 today") == "Austin, TX"
    assert location_from_text("no address here") is None


def test_detect_cms_wordpress_from_asset_paths():
    html = '<link rel="stylesheet" href="/wp-content/themes/x/style.css">'
    assert detect_cms([html]) == "wordpress"


def test_detect_cms_wordpress_from_generator_meta():
    html = '<meta name="generator" content="WordPress 6.5.2" />'
    assert detect_cms([html]) == "wordpress"


def test_detect_cms_shopify_from_footprints():
    assert detect_cms(['<img src="https://cdn.shopify.com/s/files/x.png">']) == "shopify"
    assert detect_cms(["<script>var t = Shopify.theme;</script>"]) == "shopify"


def test_detect_cms_unknown_when_no_footprint():
    assert detect_cms(["<html><body>just a site</body></html>"]) == "unknown"
    assert detect_cms([]) == "unknown"


def test_extract_facts_sets_cms_type():
    html = _LD_LOCAL + '<script src="/wp-includes/js/jquery.js"></script>'
    facts = extract_facts([FetchedDoc("https://harbor.com/", html)], domain="harbor.com")
    assert facts.cms_type == "wordpress"
    assert facts.to_dict()["cms_type"] == "wordpress"


def test_location_blocks_prefers_address_then_area_served():
    assert location_from_blocks([{"areaServed": "Greater Boston"}]) == "Greater Boston"
    assert location_from_blocks([{"address": {"addressLocality": "Reno", "addressRegion": "NV"}}]) == "Reno, NV"


def test_services_from_schema_offer_catalog():
    svcs = services_from_blocks([
        {"@type": "Service", "name": "Roof Repair"},
        {"hasOfferCatalog": {"itemListElement": [
            {"itemOffered": {"@type": "Service", "name": "Gutter Cleaning"}},
        ]}},
    ])
    assert "Roof Repair" in svcs and "Gutter Cleaning" in svcs


def test_services_from_service_page_links():
    svcs = services_from_links([FetchedDoc("https://harbor.com/", _LD_LOCAL)], "https://harbor.com/")
    assert "Teeth Whitening" in svcs and "Routine Checkups" in svcs
    assert "About" not in svcs  # /about is not a service link


def test_services_from_nav_dropdown():
    # No schema.org, no /services/<slug> links — offerings live in a nav dropdown.
    html = """
    <html><body><nav><ul>
      <li><a href="/">Home</a></li>
      <li><a href="#">Services</a>
        <ul class="dropdown">
          <li><a href="/managed-it">Managed IT</a></li>
          <li><a href="/cloud-migration">Cloud Migration</a></li>
          <li><a href="/contact">Contact</a></li>
        </ul>
      </li>
      <li><a href="/about">About</a></li>
    </ul></nav></body></html>
    """
    svcs = services_from_nav([FetchedDoc("https://acme.com/", html)])
    assert "Managed IT" in svcs and "Cloud Migration" in svcs
    assert "Contact" not in svcs  # stopword, even inside the services menu


def test_services_from_section_headings():
    # Offerings listed as sub-headings under an "Our Services" section heading.
    html = """
    <html><body><main>
      <section>
        <h2>Our Services</h2>
        <div><h3>Tax Preparation</h3><p>blah</p></div>
        <div><h3>Bookkeeping</h3><p>blah</p></div>
      </section>
      <section>
        <h2>About Us</h2>
        <h3>Our Team</h3>
      </section>
    </main></body></html>
    """
    svcs = services_from_headings([FetchedDoc("https://acme.com/", html)])
    assert "Tax Preparation" in svcs and "Bookkeeping" in svcs
    assert "Our Team" not in svcs  # belongs to the next (About) section


def test_extract_facts_combines_location_and_services():
    facts = extract_facts([FetchedDoc("https://harbor.com/", _LD_LOCAL)], domain="harbor.com")
    assert facts.location == "Austin, TX"
    assert "Teeth Whitening" in facts.services
    assert len(facts.services) <= 8


# ── industry provenance: classify the company's OWN vertical, not its customers' ──────

# A fintech whose body is full of "industries we serve: healthcare" copy. The old classifier
# read the body and called this Healthcare; the self-description (title/meta) says fintech.
_SERVED_VERTICAL_PAGE = """
<html><head>
<title>PayFlow — Payment Processing &amp; Financial Infrastructure</title>
<meta name="description" content="PayFlow is a fintech platform for online payments and billing.">
</head><body>
<h1>Get paid faster</h1>
<h2>Industries we serve</h2>
<p>We help healthcare providers, hospitals, and medical clinics collect payments.</p>
<section><h3>Healthcare</h3><p>Medical billing for clinics and dental practices.</p></section>
</body></html>
"""


def test_industry_uses_self_description_not_served_vertical_body():
    facts = extract_facts([FetchedDoc("https://payflow.com/", _SERVED_VERTICAL_PAGE)], domain="payflow.com")
    # The fintech self-description wins; the "healthcare" served-vertical body is ignored.
    assert facts.industry == "Finance"
    assert facts.industry != "Healthcare"


def test_industry_abstains_when_self_description_is_generic():
    # Generic title + only served-vertical body keywords → abstain (None) rather than guess
    # "Healthcare" off the customer copy. Empty beats confidently-wrong.
    html = """
    <html><head><title>PayFlow — Home</title></head><body>
    <h2>Our customers</h2><p>healthcare, hospitals, medical, clinics</p>
    </body></html>
    """
    facts = extract_facts([FetchedDoc("https://payflow.com/", html)], domain="payflow.com")
    assert facts.industry is None


def test_industry_ignores_contaminated_services_list():
    # A marketing platform whose "Solutions" nav lists customer SEGMENTS (Restaurants,
    # Retail). Those get scraped into `services`, but industry must come from the
    # self-description ("Email Marketing Platform") — not the served-vertical service links.
    html = """
    <html><head><title>BeeMail — Email Marketing Platform</title></head><body>
    <nav><ul><li><a>Solutions</a>
      <ul><li><a href="/solutions/restaurants">Restaurants</a></li>
          <li><a href="/solutions/retail">Retail</a></li></ul>
    </li></ul></nav>
    </body></html>
    """
    facts = extract_facts([FetchedDoc("https://beemail.com/", html)], domain="beemail.com")
    assert facts.industry == "Marketing / Advertising"
    assert facts.industry not in ("Restaurants", "Retail")


def test_industry_title_wins_over_promo_heading():
    # A gym whose <title> leads with "Gym and Fitness Club" must not be read as Education
    # because of a later "HIGH SCHOOL SUMMER PASS" promo heading. Title-first classification.
    html = """
    <html><head>
    <title>Planet Fitness | A Gym and Fitness Club for Everyone</title>
    <meta name="description" content="Affordable gym memberships starting at $15 a month.">
    </head><body>
    <h1>The Judgement Free Zone</h1>
    <h2>HIGH SCHOOL SUMMER PASS is here</h2>
    </body></html>
    """
    facts = extract_facts([FetchedDoc("https://pf.com/", html)], domain="pf.com")
    assert facts.industry == "Fitness"


def test_industry_kept_when_self_description_names_the_vertical():
    # A real vertical in the title/meta is still detected — the fix is source-aware, it does
    # not just over-abstain.
    html = """
    <html><head>
    <title>Sophos — Cybersecurity &amp; Endpoint Protection</title>
    <meta name="description" content="Next-gen endpoint protection and computer security software.">
    </head><body><h1>Stop ransomware</h1></body></html>
    """
    facts = extract_facts([FetchedDoc("https://sophos.com/", html)], domain="sophos.com")
    assert facts.industry == "Cybersecurity"


def test_competitors_from_vs_and_alternative_pages():
    html = """
    <html><head><title>Acme vs Globex — why Acme wins</title></head><body>
      <a href="/compare/acme-vs-globex">Acme vs Globex</a>
      <a href="/initech-alternative">Initech alternative</a>
      <a href="/about">About us</a>
    </body></html>
    """
    comps = competitors_from_docs([FetchedDoc("https://acme.com/compare", html)], "https://acme.com/")
    names = {c["name"] for c in comps}
    assert "Globex" in names
    assert "Initech" in names
    assert "Acme" not in names  # never propose the site's own brand


def test_competitors_empty_when_no_signals():
    html = "<html><body><a href='/pricing'>Pricing</a><p>We are great.</p></body></html>"
    assert competitors_from_docs([FetchedDoc("https://acme.com/", html)], "https://acme.com/") == []


def test_competitors_ignore_homepage_title_with_vs():
    # Regression: a homepage whose <title> merely contains "vs" must NOT yield competitors
    # ("Buy vs Rent Calculator" → phantom "Buy Calculator" / "Real Estate" before the fix).
    html = """
    <html><head><title>Buy vs Rent Calculator | XYZ Real Estate</title></head><body>
      <h1>XYZ Real Estate</h1>
      <a href="/tools/buy-vs-rent">Buy vs Rent Calculator</a>
      <a href="/listings">Browse listings</a>
    </body></html>
    """
    comps = competitors_from_docs([FetchedDoc("https://xyz.com/", html)], "https://xyz.com/")
    assert comps == []


def test_competitors_reject_generic_phrases():
    # Even on a genuine comparison page, generic category phrases aren't competitors.
    html = """
    <html><head><title>Compare options</title></head><body>
      <a href="/compare/real-estate-alternatives">Real Estate alternatives</a>
    </body></html>
    """
    comps = competitors_from_docs([FetchedDoc("https://xyz.com/compare", html)], "https://xyz.com/")
    assert all(c["name"].lower() not in {"real estate", "real", "estate"} for c in comps)


def test_competitors_from_outbound_link_anchor_text():
    # An OUTBOUND (different-domain) comparison link names the rival in its anchor TEXT,
    # not its slug, so we scan the text for non-same-site links only. Here "Globex" is
    # mined purely from the link text — the href slug ("/comparison") carries no brand.
    html = """
    <html><head><title>Compare CRMs</title></head><body>
      <a href="https://reviews.example/comparison">Acme vs Globex</a>
      <a href="https://blog.example/post">Acme vs Initech writeup</a>
    </body></html>
    """
    comps = competitors_from_docs([FetchedDoc("https://acme.com/compare", html)], "https://acme.com/")
    names = {c["name"] for c in comps}
    assert "Globex" in names  # mined from the outbound comparison link's anchor text
    assert "Initech" not in names  # /post is not a comparison slug → its text is ignored
    assert "Acme" not in names  # the site's own brand is still excluded


def test_offer_fallback_from_meta_description():
    html = (
        "<html><head><title>Acme</title>"
        "<meta name='description' content='Custom kitchen cabinetry and countertop installation for homes.'>"
        "</head><body><p>Welcome.</p></body></html>"
    )
    facts = extract_facts([FetchedDoc("https://acme.com/", html)], domain="acme.com")
    assert facts.services, "offer must never be empty after a successful crawl"
    assert "cabinetry" in facts.services[0].lower()


def test_offer_from_description_first_clause_only():
    soup_html = (
        "<html><head>"
        "<meta property='og:description' content='Bookkeeping for startups. Trusted by 500 founders.'>"
        "</head><body></body></html>"
    )
    from aeo.utils.html import parse

    out = offer_from_description(parse(soup_html))
    assert out == ["Bookkeeping for startups"]


def test_gather_site_facts_with_injected_fetch():
    pages = {
        "https://harbor.com/": _LD_LOCAL,
        "https://harbor.com/about": "<html><body>About Harbor Dental in Austin, TX 78701.</body></html>",
    }

    async def fake_fetch(url: str):
        return pages.get(url.rstrip("/") + "/" if url.endswith("harbor.com") else url) or pages.get(url)

    facts = asyncio.run(gather_site_facts("harbor.com", fetch=fake_fetch))
    assert facts.location == "Austin, TX"
    assert "Teeth Whitening" in facts.services


def test_gather_site_facts_unreachable_homepage_is_empty():
    async def dead_fetch(url: str):
        return None

    facts = asyncio.run(gather_site_facts("nope.example", fetch=dead_fetch))
    assert facts.location is None
    assert facts.services == []  # no docs crawled → fallback never engages
    assert facts.competitors == []


# ── services: drop customer SEGMENTS, keep real offerings ─────────────────────────────


def test_services_drop_customer_segments_keep_offerings():
    # A "Solutions for X" nav lists who-you-serve (sizes, served verticals, departments) —
    # not offerings. Those are dropped; the genuine offering survives.
    html = """
    <html><body><nav><ul>
      <li><a href="#">Services</a><ul>
        <li><a href="/x">Email Marketing</a></li>
        <li><a href="/x">Restaurants</a></li>
        <li><a href="/x">Small Business</a></li>
        <li><a href="/x">Engineering</a></li>
        <li><a href="/x">Managed IT</a></li>
        <li><a href="/x">See all solutions</a></li>
      </ul></li>
    </ul></nav></body></html>
    """
    svcs = services_from_nav([FetchedDoc("https://acme.com/", html)])
    svcs = [s for s in svcs if s]
    assert "Email Marketing" in svcs and "Managed IT" in svcs  # real offerings kept
    # served segments / nav CTA dropped (exact-match, so multi-word "Managed IT" is safe)
    for seg in ("Restaurants", "Small Business", "Engineering", "See all solutions"):
        assert seg not in svcs


def test_offer_fallback_truncates_on_word_boundary():
    # A long meta description is cut at a word boundary, never mid-word ("…care, incl").
    html = (
        "<html><head><meta name='description' "
        "content='We help patients get affordable dental care, including checkups and dentures.'>"
        "</head><body></body></html>"
    )
    from aeo.utils.html import parse

    out = offer_from_description(parse(html))
    assert out and len(out[0]) <= 60
    assert out[0] == out[0].rstrip()  # no trailing partial fragment / stray space
    # the cut lands between whole words — the last token is complete
    assert not out[0].endswith("includ") and not out[0].endswith("inclu")


def test_offer_fallback_rejects_nav_stopword_title():
    # "Home | Jones Day" → first clause "Home" is a nav label, not an offer → no fallback.
    html = "<html><head><title>Home | Jones Day</title></head><body></body></html>"
    from aeo.utils.html import parse

    assert offer_from_description(parse(html)) == []


# ── bot-wall / challenge stub detection ───────────────────────────────────────────────


def test_is_challenge_page_detects_small_stub():
    assert is_challenge_page("<html><head><title>Just a moment...</title></head><body></body></html>")
    assert is_challenge_page("<html><body>Client Challenge</body></html>")
    assert is_challenge_page(None)
    assert is_challenge_page("")


def test_is_challenge_page_keeps_real_page():
    # A large page is real even if it happens to mention a marker phrase in copy.
    big = "<html><body>" + ("Our access denied policy explained. " * 1000) + "</body></html>"
    assert not is_challenge_page(big)
    assert not is_challenge_page("<html><body>A normal small business homepage.</body></html>")


def test_gather_site_facts_skips_challenge_homepage():
    async def challenge_fetch(url: str):
        return "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"

    facts = asyncio.run(gather_site_facts("walled.example", fetch=challenge_fetch))
    assert facts.services == [] and facts.industry is None  # no garbage from the stub


def test_playwright_fallback_recovers_blocked_homepage(monkeypatch):
    # On the real path (no injected fetch), a blocked httpx homepage falls back to a headless
    # render. Both fetchers are monkeypatched, so no real browser/network is launched.
    import aeo.crawl.discovery as disc

    async def httpx_blocked(url: str):
        return "<html><head><title>Just a moment...</title></head><body>checking your browser</body></html>"

    async def rendered(url: str, **kw):
        return (
            "<html><head><title>Acme Plumbing — Drain Cleaning &amp; Repair</title></head>"
            "<body><h1>Trusted local plumbers</h1></body></html>"
        )

    monkeypatch.setattr(disc, "browser_fetch_text", httpx_blocked)
    monkeypatch.setattr(disc, "playwright_fetch_text", rendered)
    facts = asyncio.run(gather_site_facts("acme.example"))  # not injected → fallback active
    assert facts.industry == "Construction"  # "plumbing" recovered from the rendered title


def test_browser_pool_close_is_safe_when_unused():
    # The shared pool is closed on API shutdown; closing a never-launched pool is a no-op.
    from aeo.crawl.browser_pool import close_pool

    asyncio.run(close_pool())
    asyncio.run(close_pool())  # idempotent — must not raise


def test_playwright_fallback_not_used_when_fetch_injected():
    # Injecting a fetch (tests/custom callers) must never trigger the real-browser fallback.
    calls = {"n": 0}

    async def injected(url: str):
        calls["n"] += 1
        return None  # always blocked

    facts = asyncio.run(gather_site_facts("acme.example", fetch=injected))
    assert facts.industry is None and facts.services == []
    assert calls["n"] >= 1  # the injected fetch was used; no playwright detour


# ── schema.org @type → vertical (self-declared, wins over keywords) ──────────────


def test_vertical_from_schema_types_maps_business_subtypes():
    from aeo.intelligence.site_facts import vertical_from_schema_types

    assert vertical_from_schema_types([{"@type": "Dentist"}]) == "Healthcare"
    assert vertical_from_schema_types([{"@type": ["Organization", "Restaurant"]}]) == "Restaurants"
    assert vertical_from_schema_types([{"@type": "LegalService"}]) == "Legal"
    # Generic containers carry no vertical — fall through to the keyword classifier.
    assert vertical_from_schema_types([{"@type": "Organization"}, {"@type": "WebSite"}]) is None
    assert vertical_from_schema_types([]) is None


def test_industry_schema_type_wins_over_title_keyword():
    # The homepage declares WHAT THE BUSINESS IS via JSON-LD; a marketing title that
    # happens to carry another vertical's keyword ("software") must not override it.
    html = (
        "<html><head><title>Fresh Bites — ordering software built into every table</title>"
        '<script type="application/ld+json">{"@type": "Restaurant", "name": "Fresh Bites"}</script>'
        "</head><body><h1>Neighborhood kitchen</h1></body></html>"
    )
    facts = extract_facts([FetchedDoc("https://freshbites.com/", html)], domain="freshbites.com")
    assert facts.industry == "Restaurants"


def test_industry_ignores_key_page_jsonld_types():
    # Vertical-specific types on a KEY page describe content, not the company — a
    # payments company's /solutions/restaurants page must not make it a Restaurant.
    home = (
        "<html><head><title>PayFlow</title></head>"
        "<body><h1>Payments infrastructure</h1></body></html>"
    )
    key = (
        "<html><head><title>Restaurants | PayFlow</title>"
        '<script type="application/ld+json">{"@type": "Restaurant", "name": "Demo"}</script>'
        "</head><body></body></html>"
    )
    facts = extract_facts(
        [
            FetchedDoc("https://payflow.com/", home),
            FetchedDoc("https://payflow.com/solutions/restaurants", key),
        ],
        domain="payflow.com",
    )
    assert facts.industry is None  # homepage gives no signal; key-page type ignored


# ── first_clause (the shared offer trimmer) ──────────────────────────────────────


def test_first_clause_drops_dangling_function_words():
    from aeo.intelligence.site_facts import first_clause

    # A 60-char cut would strand "based in" — the trimmer drops the dangling tail.
    assert (
        first_clause("medical practice and medical research group based in Rochester, Minnesota")
        == "medical practice and medical research group"
    )


def test_first_clause_rejects_empty_and_stopword_only():
    from aeo.intelligence.site_facts import first_clause

    assert first_clause("") is None
    assert first_clause("Home") is None  # nav stopword, not an offer


def test_first_clause_strips_subject_prose_prefix():
    from aeo.intelligence.site_facts import first_clause

    # A description names its subject; the offer wants the predicate.
    assert (
        first_clause("Stripe is a financial infrastructure platform for businesses")
        == "financial infrastructure platform for businesses"
    )
    # No copula-with-article → left intact (mission prose is not a subject prefix).
    assert first_clause("Harvard is devoted to excellence in teaching") == (
        "Harvard is devoted to excellence in teaching"
    )


def test_challenge_page_detects_system_down_interstitial():
    from aeo.intelligence.site_facts import is_challenge_page

    # fedex.com served a 200 interstitial titled "System Down" — junk, not the site.
    assert is_challenge_page("<html><head><title>FedEx | System Down</title></head><body></body></html>")
    # …but the marker is TITLE-anchored: an IT-support site asking the phrase in body
    # copy is a real page and must not be blanked.
    assert not is_challenge_page(
        "<html><head><title>Acme IT Support</title></head>"
        "<body><h1>Is your system down? We can help.</h1></body></html>"
    )


def test_vertical_from_schema_types_ignores_app_promo_types():
    from aeo.intelligence.site_facts import vertical_from_schema_types

    # A restaurant/bank/gym promoting its mobile app embeds SoftwareApplication on the
    # homepage — deliberately unmapped so it can't override the true vertical.
    assert vertical_from_schema_types([{"@type": "SoftwareApplication"}]) is None
    assert vertical_from_schema_types([{"@type": ["MobileApplication", "Restaurant"]}]) == "Restaurants"


def test_location_rejects_entity_uri_values():
    from aeo.intelligence.site_facts import location_from_blocks

    # sameAs-style Wikidata URIs inside areaServed/address are references, not places.
    assert location_from_blocks([{"areaServed": "http://www.wikidata.org/entity/Q13780930"}]) is None
    assert location_from_blocks([{"address": "https://example.com/contact"}]) is None
    assert location_from_blocks([{"areaServed": "Greater Cleveland"}]) == "Greater Cleveland"
