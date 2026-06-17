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
    extract_facts,
    gather_site_facts,
    location_from_blocks,
    location_from_text,
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
    assert facts.services == []
    assert facts.competitors == []
