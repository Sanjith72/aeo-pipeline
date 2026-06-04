"""
Unit tests for the adversarial auditor (ported idea), fully offline.

Covers: URL extraction (dedupe + trailing-punct strip), the deterministic
citation-hallucination checks (structural validity + injected reachability), and
the audit's verdict logic — model refutes / passes / unavailable, hallucination
overrides an LLM pass, and the deterministic-fallback when no LLM is present.
No network: reachability is exercised through an injected ``head_check``.
"""

from __future__ import annotations

from aeo.validation.adversarial import (
    adversarial_audit,
    check_citation_signals,
    extract_citation_urls,
)


class FakeLLM:
    """generate_json returns a canned dict (or None to simulate failure)."""

    def __init__(self, result, *, enabled=True):
        self._result = result
        self._enabled = enabled
        self.calls = 0

    @property
    def enabled(self):
        return self._enabled

    @property
    def model(self):
        return "fake-model"

    def generate_json(self, prompt, system=None):
        self.calls += 1
        return self._result


class TestExtractUrls:
    def test_dedupe_and_strip_trailing_punct(self):
        urls = extract_citation_urls(
            "see https://a.com/x. and https://a.com/x, also https://b.org)"
        )
        assert urls == ["https://a.com/x", "https://b.org"]

    def test_empty_text(self):
        assert extract_citation_urls("") == []
        assert extract_citation_urls("no links here") == []


class TestCitationSignals:
    def test_valid_url_not_hallucinated(self):
        sig = check_citation_signals(["https://example.com/p"])[0]
        assert sig.structurally_valid is True
        assert sig.reachable is None  # not verified
        assert sig.hallucinated is False

    def test_malformed_url_is_hallucinated(self):
        sig = check_citation_signals(["http://nodot"])[0]  # netloc has no dot
        assert sig.structurally_valid is False
        assert sig.hallucinated is True

    def test_unreachable_valid_url_flagged(self):
        sig = check_citation_signals(
            ["https://example.com/p"], verify_reachability=True, head_check=lambda u: False
        )[0]
        assert sig.structurally_valid is True
        assert sig.reachable is False
        assert sig.hallucinated is True

    def test_reachable_valid_url_clean(self):
        sig = check_citation_signals(
            ["https://example.com/p"], verify_reachability=True, head_check=lambda u: True
        )[0]
        assert sig.reachable is True
        assert sig.hallucinated is False


class TestAdversarialAudit:
    def test_deterministic_only_passes_when_clean(self):
        v = adversarial_audit("Cite https://nvd.nist.gov for CVSS.", llm=None)
        assert v.passed is True
        assert v.available is False
        assert v.llm_passed is None
        assert v.attempts == 0

    def test_deterministic_flags_hallucinated_citation(self):
        v = adversarial_audit("Add a citation to http://nodot here.", llm=None)
        assert v.passed is False
        assert v.hallucinated_count == 1
        assert "hallucinated" in (v.failure_reason or "")

    def test_llm_refutes_fails(self):
        llm = FakeLLM({"passed": False, "failure_reason": "unsupported claim"})
        v = adversarial_audit("Proposal text with https://a.com/x", llm=llm)
        assert v.available is True
        assert v.llm_passed is False
        assert v.passed is False
        assert "unsupported claim" in (v.failure_reason or "")

    def test_llm_passes(self):
        llm = FakeLLM({"passed": True, "failure_reason": None})
        v = adversarial_audit("Solid proposal citing https://attack.mitre.org", llm=llm)
        assert v.available is True
        assert v.llm_passed is True
        assert v.passed is True
        assert v.failure_reason is None

    def test_hallucinated_citation_overrides_llm_pass(self):
        llm = FakeLLM({"passed": True})
        v = adversarial_audit("Cite http://nodot (bad).", llm=llm)
        assert v.llm_passed is True
        assert v.passed is False  # hallucinated citation still fails it
        assert v.hallucinated_count == 1

    def test_llm_unavailable_degrades_to_deterministic(self):
        llm = FakeLLM(None, enabled=True)  # generate_json always None → retries exhaust
        v = adversarial_audit("Clean proposal https://a.com/x", llm=llm, max_attempts=3)
        assert llm.calls == 3
        assert v.available is False
        assert v.llm_passed is None
        assert v.passed is True  # falls back to citation-only check (clean)

    def test_disabled_llm_not_called(self):
        llm = FakeLLM({"passed": False}, enabled=False)
        v = adversarial_audit("Clean proposal https://a.com/x", llm=llm)
        assert llm.calls == 0
        assert v.available is False
        assert v.passed is True

    def test_to_detail_shape(self):
        v = adversarial_audit("Cite http://nodot", llm=None)
        d = v.to_detail()
        assert d["passed"] is False
        assert d["hallucinated"] == 1
        assert isinstance(d["citation_signals"], list) and d["citation_signals"]
