"""Intake intelligence (#2, #3) — inference + crawl-quality routing, all offline.

The functions in ``intelligence.intake`` are pure (no DB/network), so the
derive-don't-ask behaviour and the rich/thin/dead routing are fully unit-testable.
The API-level routing test lives in tests/unit/test_api.py (needs the TestClient).
"""

from __future__ import annotations

from types import SimpleNamespace

from aeo.intelligence import build_site_profile
from aeo.intelligence.intake import (
    DEAD,
    RICH,
    THIN,
    classify_intake,
    infer_industry,
    infer_location,
    total_word_count,
)
from aeo.intelligence.signals import to_page_views


def _pv(*urls: str):
    return to_page_views([SimpleNamespace(url=u, page_type="default") for u in urls])


class TestClassifyIntake:
    def test_dead_when_no_pages(self):
        assert classify_intake(0, None, min_pages=5, min_words=300) == DEAD

    def test_thin_when_below_min_pages(self):
        assert classify_intake(3, None, min_pages=5, min_words=300) == THIN

    def test_rich_when_enough_pages(self):
        assert classify_intake(12, None, min_pages=5, min_words=300) == RICH

    def test_thin_on_low_word_count_even_with_enough_pages(self):
        assert classify_intake(12, 120, min_pages=5, min_words=300) == THIN

    def test_word_count_ignored_when_unknown(self):
        # The live profile path can't fetch text → passes None → page count alone gates.
        assert classify_intake(12, None, min_pages=5, min_words=300) == RICH


class TestInferIndustry:
    def test_explicit_category_overrides(self):
        assert infer_industry([], category="Cybersecurity", topic="x", business_model="saas") == "Cybersecurity"

    def test_specific_topic_used_when_no_category(self):
        assert infer_industry([], topic="Vulnerability Management", business_model="saas") == "Vulnerability Management"

    def test_generic_topic_falls_back_to_model_label(self):
        assert infer_industry([], topic="generic", business_model="saas") == "Software / SaaS"

    def test_none_when_nothing_known(self):
        assert infer_industry([], topic="", business_model=None) is None


class TestInferLocation:
    def test_location_slug_is_humanized(self):
        pages = _pv("https://acme.com/locations/new-york", "https://acme.com/about")
        assert infer_location(pages) == "New York"

    def test_singular_location_path(self):
        assert infer_location(_pv("https://acme.com/location/austin")) == "Austin"

    def test_none_without_a_location_signal(self):
        # A SaaS with no location encoded in its paths → honest None, not a guess.
        assert infer_location(_pv("https://acme.com/pricing", "https://acme.com/blog/x")) is None

    def test_stopword_segment_is_skipped(self):
        assert infer_location(_pv("https://acme.com/locations/near-me")) is None


class TestTotalWordCount:
    def test_sums_and_ignores_empties(self):
        assert total_word_count(["a b c", "", None, "d e"]) == 5


class TestSiteProfileCarriesIndustryLocation:
    def test_local_site_infers_both(self):
        discovered = [
            SimpleNamespace(url=u, page_type=t)
            for u, t in [
                ("https://corner.com/", "homepage"),
                ("https://corner.com/locations/boston", "default"),
                ("https://corner.com/book", "default"),
                ("https://corner.com/contact", "contact"),
            ]
        ]
        prof = build_site_profile(domain="corner.com", discovered=discovered, topic="restaurant")
        d = prof.to_dict()
        assert d["location"] == "Boston"
        assert d["industry"]  # topic-derived ("restaurant")

    def test_explicit_location_override_wins(self):
        discovered = [SimpleNamespace(url="https://corner.com/locations/boston", page_type="default")]
        prof = build_site_profile(domain="corner.com", discovered=discovered, location="Cambridge")
        assert prof.location == "Cambridge"

    def test_saas_site_has_no_location(self):
        discovered = [
            SimpleNamespace(url=u, page_type="default")
            for u in ["https://acme.com/", "https://acme.com/pricing", "https://acme.com/docs"]
        ]
        prof = build_site_profile(domain="acme.com", discovered=discovered, topic="B2B SaaS")
        assert prof.location is None
        assert prof.industry == "B2B SaaS"
