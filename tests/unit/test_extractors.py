"""
Extractor-level tests.

Each fixture is run through the full extractor stack (via the ``*_ctx``
fixtures in conftest) and we assert the raw signals each extractor emits —
this is the contract the scorers depend on.
"""

from __future__ import annotations


class TestSchemaJsonld:
    def test_strong_collects_valued_types(self, strong_ctx):
        s = strong_ctx.bundle.get("schema_jsonld")
        assert s["invalid_blocks"] == 0
        assert {"TechArticle", "FAQPage", "BreadcrumbList", "Organization"} <= set(s["types"])
        assert "Jane Doe" in s["authors"]
        assert s["dates"]  # datePublished + dateModified surfaced for eeat fallback

    def test_weak_has_no_structured_data(self, weak_ctx):
        s = weak_ctx.bundle.get("schema_jsonld")
        assert s["block_count"] == 0
        assert s["types"] == []


class TestQaBlocks:
    def test_strong_pairs_question_headings_with_answers(self, strong_ctx):
        qa = strong_ctx.bundle.get("qa_blocks")
        assert qa["pair_count"] == 4
        assert qa["question_heading_count"] == 4

    def test_weak_has_no_pairs(self, weak_ctx):
        assert weak_ctx.bundle.get("qa_blocks")["pair_count"] == 0


class TestStats:
    def test_strong_counts_distinct_concrete_stats(self, strong_ctx):
        st = strong_ctx.bundle.get("stats")
        assert st["count"] >= 10
        # A spread of kinds, not just one repeated pattern.
        assert {"percent", "money", "cve", "cvss"} <= set(st["by_kind"])

    def test_weak_finds_no_stats(self, weak_ctx):
        assert weak_ctx.bundle.get("stats")["count"] == 0


class TestEntities:
    def test_strong_is_entity_dominant(self, strong_ctx):
        en = strong_ctx.bundle.get("entities")
        assert en["primary"]["name"] == "Securin"
        assert en["entity_count"] >= 8
        assert en["first_person_count"] == 3
        assert en["ratio"] >= 2.5

    def test_weak_is_first_person_dominant(self, weak_ctx):
        en = weak_ctx.bundle.get("entities")
        assert en["entity_count"] == 0
        assert en["first_person_count"] >= 5
        assert en["ratio"] == 0.0

    def test_glossary_named_with_no_first_person(self, glossary_ctx):
        en = glossary_ctx.bundle.get("entities")
        assert en["first_person_count"] == 0
        assert en["ratio"] is None  # zero first-person → ratio undefined, not zero


class TestHeadings:
    def test_strong_question_ratio(self, strong_ctx):
        h = strong_ctx.bundle.get("headings")
        assert h["missing_h1"] is False
        assert h["template_h1"] is False
        assert h["h23_total"] == 6
        assert h["h23_question_ratio"] >= 0.65

    def test_weak_template_h1_detected(self, weak_ctx):
        h = weak_ctx.bundle.get("headings")
        assert h["h1_text"] == "Resources"
        assert h["template_h1"] is True
        assert h["missing_h1"] is False


class TestEeat:
    def test_strong_has_author_date_credentials(self, strong_ctx):
        e = strong_ctx.bundle.get("eeat")
        assert e["has_author"] is True
        assert e["has_date"] is True
        assert "CISSP" in e["credentials_mentioned"]

    def test_weak_has_no_eeat_signals(self, weak_ctx):
        e = weak_ctx.bundle.get("eeat")
        assert e["has_author"] is False
        assert e["has_date"] is False


class TestLinks:
    def test_strong_finds_five_authority_links(self, strong_ctx):
        lk = strong_ctx.bundle.get("links")
        assert lk["authority_count"] == 5
        # nvd.nist.gov matches both "nist.gov" and "nvd.nist.gov" in config →
        # attributed to the most specific (longest) match, deterministically.
        assert {"nvd.nist.gov", "cisa.gov", "mitre.org", "owasp.org", "sans.org"} <= set(lk["authority_domains"])

    def test_weak_has_no_authority_links(self, weak_ctx):
        assert weak_ctx.bundle.get("links")["authority_count"] == 0


class TestGlossary:
    def test_glossary_opportunity_flagged(self, glossary_ctx):
        g = glossary_ctx.bundle.get("glossary")
        assert g["total_definitions"] >= 20
        assert g["is_glossary_candidate"] is True
        assert g["has_defined_term_schema"] is False
        assert g["opportunity"] is True

    def test_strong_is_not_a_glossary(self, strong_ctx):
        assert strong_ctx.bundle.get("glossary")["opportunity"] is False
