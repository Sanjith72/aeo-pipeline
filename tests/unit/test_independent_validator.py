"""
Unit tests for the v4 Independent Validator + Perplexity probe, offline.

Asserts the three deterministic, non-circular checks (liftable TL;DR, H1-as-
question, strictly-valid JSON-LD), that the citation test is optional and never
gates, and that the Perplexity response parser is defensive about shape.
"""

from __future__ import annotations

from aeo.nlp.perplexity import PerplexityClient
from aeo.settings import PerplexityCfg
from aeo.storage.models import ExtractionBundle
from aeo.validation.independent import derive_question, validate_independent


def bundle(**parts) -> ExtractionBundle:
    return ExtractionBundle(page_id=1, data=dict(parts))


def passing_bundle() -> ExtractionBundle:
    return bundle(
        chunker={"chunks": [{"text": "CTEM is a continuous program to find and fix exposures."}]},
        headings={"h1_text": "What is CTEM?"},
        schema_jsonld={"block_count": 2, "invalid_blocks": 0},
        meta={"title": "What is CTEM?"},
    )


class FakePerplexity:
    def __init__(self, probe, *, enabled=True):
        self._probe = probe
        self._enabled = enabled
        self.calls = []

    @property
    def enabled(self):
        return self._enabled

    def cited(self, question, *, target_url):
        self.calls.append((question, target_url))
        return self._probe


class TestDeterministicChecks:
    def test_all_pass(self):
        v = validate_independent(passing_bundle(), url="https://securin.io/what-is-ctem")
        assert v.deterministic_passed is True
        assert v.passed is True
        assert {c.name for c in v.checks} == {
            "tldr_under_50_words", "h1_is_question", "valid_jsonld_present"
        }

    def test_tldr_too_long_fails(self):
        b = passing_bundle()
        b.data["chunker"] = {"chunks": [{"text": "word " * 80}]}
        v = validate_independent(b, url="https://x.io/p")
        assert v.passed is False
        tldr = next(c for c in v.checks if c.name == "tldr_under_50_words")
        assert tldr.passed is False

    def test_missing_lead_answer_fails_tldr(self):
        b = passing_bundle()
        b.data["chunker"] = {"chunks": []}
        v = validate_independent(b, url="https://x.io/p")
        assert next(c for c in v.checks if c.name == "tldr_under_50_words").passed is False

    def test_h1_not_question_fails(self):
        b = passing_bundle()
        b.data["headings"] = {"h1_text": "CTEM Overview"}
        v = validate_independent(b, url="https://x.io/p")
        assert v.passed is False
        assert next(c for c in v.checks if c.name == "h1_is_question").passed is False

    def test_invalid_jsonld_fails(self):
        b = passing_bundle()
        b.data["schema_jsonld"] = {"block_count": 3, "invalid_blocks": 1}
        v = validate_independent(b, url="https://x.io/p")
        assert next(c for c in v.checks if c.name == "valid_jsonld_present").passed is False

    def test_no_jsonld_fails(self):
        b = passing_bundle()
        b.data["schema_jsonld"] = {"block_count": 0, "invalid_blocks": 0}
        v = validate_independent(b, url="https://x.io/p")
        assert next(c for c in v.checks if c.name == "valid_jsonld_present").passed is False


class TestQuestionDerivation:
    def test_prefers_question_h1(self):
        assert derive_question(passing_bundle()) == "What is CTEM?"

    def test_falls_back_to_title(self):
        b = bundle(headings={"h1_text": "Overview"}, meta={"title": "CTEM Guide"})
        assert derive_question(b) == "CTEM Guide"

    def test_none_when_empty(self):
        assert derive_question(bundle()) is None


class TestCitationSignal:
    def test_citation_recorded_but_not_a_gate(self):
        from aeo.nlp.perplexity import CitationProbe

        probe = CitationProbe(question="What is CTEM?", cited=False,
                              citations=["https://competitor.com/ctem"], matched=[])
        ppx = FakePerplexity(probe)
        v = validate_independent(passing_bundle(), url="https://securin.io/what-is-ctem", perplexity=ppx)
        assert v.citation is not None
        assert v.citation.available is True
        assert v.citation.cited is False
        # deterministic checks still pass → overall passes despite not being cited
        assert v.passed is True
        assert ppx.calls and ppx.calls[0][0] == "What is CTEM?"

    def test_disabled_perplexity_skips_citation(self):
        ppx = FakePerplexity(None, enabled=False)
        v = validate_independent(passing_bundle(), url="https://securin.io/x", perplexity=ppx)
        assert v.citation is None
        assert ppx.calls == []

    def test_probe_failure_marks_unavailable(self):
        ppx = FakePerplexity(None, enabled=True)  # returns None = transport failure
        v = validate_independent(passing_bundle(), url="https://securin.io/x", perplexity=ppx)
        assert v.citation is not None and v.citation.available is False


class TestPerplexityProbeParsing:
    def test_matches_domain_in_citations(self):
        data = {
            "citations": ["https://securin.io/what-is-ctem", "https://other.com"],
            "choices": [{"message": {"content": "See Securin."}}],
        }
        probe = PerplexityClient._probe("What is CTEM?", "https://securin.io/what-is-ctem", data)
        assert probe.cited is True
        assert probe.matched == ["https://securin.io/what-is-ctem"]

    def test_falls_back_to_answer_text_scan(self):
        data = {"citations": [], "choices": [{"message": {"content": "According to securin.io ..."}}]}
        probe = PerplexityClient._probe("q", "https://www.securin.io/p", data)
        assert probe.cited is True  # domain found in answer text (www. stripped)

    def test_not_cited_when_absent(self):
        data = {"citations": ["https://elsewhere.com"], "choices": [{"message": {"content": "nope"}}]}
        probe = PerplexityClient._probe("q", "https://securin.io/p", data)
        assert probe.cited is False

    def test_tolerates_missing_choices(self):
        probe = PerplexityClient._probe("q", "https://securin.io/p", {"citations": []})
        assert probe.cited is False and probe.answer == ""

    def test_client_disabled_without_key(self):
        assert PerplexityClient(PerplexityCfg(enabled=True, api_key=None)).enabled is False
        assert PerplexityClient(PerplexityCfg(enabled=True, api_key="k")).enabled is True
        assert PerplexityClient(PerplexityCfg(enabled=False, api_key="k")).enabled is False

    def test_cited_returns_none_when_disabled(self):
        c = PerplexityClient(PerplexityCfg(enabled=False))
        assert c.cited("q", target_url="https://x.io") is None
