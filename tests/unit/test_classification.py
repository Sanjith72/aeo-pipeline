"""Website Classification Engine — tiers + structural quality."""

from __future__ import annotations

import pytest

from aeo.intelligence.classification import (
    SiteClass,
    build_classification,
    classify_tier,
    detect_archetypes,
)
from aeo.intelligence.signals import page_view


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, SiteClass.NONE),
        (1, SiteClass.SINGLE_PAGE),
        (2, SiteClass.SMALL),
        (10, SiteClass.SMALL),
        (11, SiteClass.MEDIUM),
        (50, SiteClass.MEDIUM),
        (51, SiteClass.LARGE),
        (200, SiteClass.LARGE),
        (201, SiteClass.ENTERPRISE),
        (5000, SiteClass.ENTERPRISE),
    ],
)
def test_classify_tier_boundaries(count: int, expected: SiteClass) -> None:
    assert classify_tier(count) is expected


def test_detect_archetypes_by_page_type_and_slug_token() -> None:
    pages = [
        page_view("https://x.com/about", "about"),
        page_view("https://x.com/contact-us", "default"),  # by slug token
        page_view("https://x.com/solutions/ctem", "solution"),  # services by type
        page_view("https://x.com/industries/finance", "default"),  # by token "industr"
        page_view("https://x.com/faq", "default"),
        page_view("https://x.com/blog/post", "blog"),
    ]
    present = detect_archetypes(pages)
    assert present["about"] is True
    assert present["contact"] is True
    assert present["services"] is True
    assert present["industries"] is True
    assert present["faq"] is True
    assert present["blog"] is True
    assert present["case_studies"] is False  # nothing matched


def test_token_hit_is_word_bounded_not_naive_substring() -> None:
    # "shop" must NOT fire on "/workshop" (single-word token = whole-word match);
    # a phrase token matches as a substring. This is the signal-level guard against
    # false positives like a workshop page reading as an ecommerce 'shop'.
    from aeo.intelligence.signals import token_hit

    assert token_hit("/workshop", "shop") is False
    assert token_hit("/shop", "shop") is True
    assert token_hit("/case-studies/acme", "case-stud") is True  # phrase = substring
    # phrase tokens match underscore paths too (_ treated as -)
    assert token_hit("/case_studies/acme", "case-stud") is True
    # prefix/stem tokens (trailing '*') match any word starting with the stem
    assert token_hit("/industry/finance", "industr*") is True
    assert token_hit("/industries/finance", "industr*") is True
    assert token_hit("/industrial/iot", "industr*") is True
    assert token_hit("/about", "industr*") is False


def test_industries_archetype_detects_singular_industry_path() -> None:
    # Regression: the 'industr' stem token used to be dead (whole-word match never
    # fired); a singular /industry/<vertical> path must now count as the archetype.
    assert detect_archetypes([page_view("https://x.com/industry/finance", "default")])["industries"] is True
    assert detect_archetypes([page_view("https://x.com/case_studies/acme", "default")])["case_studies"] is True


def test_structure_score_relative_to_business_model() -> None:
    # A SaaS expects [about, contact, product, resources, faq, case_studies]; supply 3.
    pages = [
        page_view("https://x.com/about", "about"),
        page_view("https://x.com/pricing", "product"),  # product archetype
        page_view("https://x.com/docs", "default"),  # resources? "docs" not a resources token
        page_view("https://x.com/faq", "default"),
    ]
    c = build_classification(pages=pages, site_class=SiteClass.SMALL, model="saas")
    # present among expected: about, product, faq  -> 3 of 6
    assert c.structure.structure_score == pytest.approx(round(3 / 6, 3))
    assert "contact" in c.structure.missing_archetypes
    assert "case_studies" in c.structure.missing_archetypes
    assert "about" not in c.structure.missing_archetypes


def test_empty_site_is_none_tier_with_zero_structure() -> None:
    c = build_classification(pages=[], site_class=classify_tier(0), model="lead_gen")
    assert c.site_class is SiteClass.NONE
    assert c.structure.page_count == 0
    assert c.structure.structure_score == 0.0
    # everything expected for lead_gen is missing
    assert "about" in c.structure.missing_archetypes


def test_type_distribution_counts() -> None:
    pages = [
        page_view("https://x.com/a", "blog"),
        page_view("https://x.com/b", "blog"),
        page_view("https://x.com/c", "product"),
    ]
    c = build_classification(pages=pages, site_class=SiteClass.SMALL, model="lead_gen")
    assert c.structure.type_distribution == {"blog": 2, "product": 1}
