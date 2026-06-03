"""Smoke test for the AEO v4 production core (Blocks 1-4).

No DB, no live LLM, no network. Verifies:
  1. Pydantic models instantiate and validate (Block 1 blueprint + lock + taxonomy).
  2. The crawler computes & stores a SHA-256 content hash via a mocked HTTP response (Block 2).
  3. The 4-track gap fan-out runs with engine_target='perplexity' and a mocked LLM (Block 3).
  4. The independent auditor returns an AuditReport from a mocked adversarial verdict (Block 4).

Run:  python smoke_test.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

# Make the src/ layout importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent / "src"))

import aeo.agents.crawler as crawler_mod  # noqa: E402
import aeo.agents.coverage_diff as gap_mod  # noqa: E402
from aeo.agents.coverage_diff import evaluate_gaps  # noqa: E402
from aeo.agents.crawler import _content_hash, prioritize_urls  # noqa: E402
from aeo.agents.validator import (  # noqa: E402
    audit_recommendation,
    check_citation_signals,
    extract_citation_urls,
)
from aeo.models.blueprint import (  # noqa: E402
    AuditReport,
    Blueprint,
    BLUEPRINT_LOCK_DAYS,
    CompetitorIntel,
    ContentSection,
    CrawledPage,
    EngineTarget,
    GapAnalysisReport,
    Recommendation,
    TaxonomyTag,
)


def _dummy_blueprint() -> Blueprint:
    return Blueprint(
        domain="securin.io",
        engine_target=EngineTarget.PERPLEXITY,
        taxonomy_tags=[TaxonomyTag.MITRE_INITIAL_ACCESS, TaxonomyTag.CVSS_CRITICAL,
                       TaxonomyTag.CISA_KEV_LISTED],
        target_queries=["what is continuous threat exposure management",
                        "how does penetration testing as a service work"],
        required_entities=["Securin", "CVSS", "CISA KEV", "MITRE ATT&CK"],
        schema_types=["FAQPage", "Article"],
        content_sections=[ContentSection(heading="What is CTEM?", type="definition",
                                         required_entities=["CTEM"], min_words=200)],
        citation_sources=["https://www.cisa.gov/known-exploited-vulnerabilities-catalog"],
        competitor_intel=[CompetitorIntel(domain="pentera.io",
                                          structural_patterns=["pillar+cluster"],
                                          observed_entities=["RBVM"])],
        freshness_days=7,
    )


def test_block1_models() -> None:
    bp = _dummy_blueprint()
    # blueprint_id / locked_until / content_hash are computed fields
    assert bp.blueprint_id == str(bp.id)
    assert bp.locked_until == bp.created_at + timedelta(days=BLUEPRINT_LOCK_DAYS)
    assert len(bp.content_hash) == 64  # SHA-256 hex → CHAR(64)
    assert bp.engine_target is EngineTarget.PERPLEXITY
    assert bp.is_locked is True  # freshly created → inside 30-day window

    # taxonomy tags serialize as their string values
    dumped = bp.model_dump()
    assert "mitre:initial-access" in dumped["taxonomy_tags"]
    assert dumped["content_hash"] == bp.content_hash

    # 30-day lock: core structure is immutable (frozen) — reassignment must raise
    raised = False
    try:
        bp.target_queries = ["mutated"]  # type: ignore[misc]
    except Exception:
        raised = True
    assert raised, "core blueprint field should be frozen/immutable inside the lock window"
    print("  [Block 1] Blueprint models, taxonomy, 30-day lock & content_hash OK")


def test_block2_sha256_gate() -> None:
    body = "Securin delivers continuous threat exposure management."
    h = _content_hash(body)
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)

    # change gate: same content → unchanged; different content → changed
    assert _content_hash(body) == h
    assert _content_hash(body + " updated") != h

    # async fetch with a mocked HTTP client → asserts hash is computed & stored on the page
    class _Resp:
        text = f"<html><head><title>Securin</title></head><body><h1>What is CTEM?</h1>{body}</body></html>"
        def raise_for_status(self) -> None: ...

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True) -> _Resp:
            return _Resp()

    async def _run() -> CrawledPage:
        known: dict[str, str] = {}
        page = await crawler_mod._fetch(_Client(), "https://securin.io/ctem", known)
        known[page.url] = page.content_hash  # store hash back (gate persistence)
        return page

    page = asyncio.run(_run())
    assert len(page.content_hash) == 64
    assert page.changed is True  # not previously known
    # Top-N prioritization derives a URL list from a blueprint
    ranked = prioritize_urls([page], _dummy_blueprint(), top_n=10)
    assert ranked == [page.url]
    print("  [Block 2] SHA-256 gate, mocked fetch hash storage & Top-N prioritization OK")


def test_block3_fanout_perplexity() -> None:
    bp = _dummy_blueprint()
    page = CrawledPage(
        url="https://securin.io/ctem",
        content_hash=_content_hash("x"),
        title="What is CTEM?",
        headings=["What is CTEM?"],
        body_text=("Securin explains continuous threat exposure management using CVSS and "
                   "CISA KEV signals mapped to MITRE ATT&CK. " * 30),
        schema_markup=[{"@type": "Article",
                        "dateModified": datetime.utcnow().isoformat()}],
        links=["https://www.cisa.gov/known-exploited-vulnerabilities-catalog"],
    )

    # Mock the LLM content-gap track so no Ollama call is made.
    async def _fake_ollama(self, url, json, **kwargs):  # noqa: ANN001
        class _R:
            status_code = 200
            def raise_for_status(self) -> None: ...
            @staticmethod
            def json() -> dict:
                return {"message": {"content": '{"coverage": [{"query": "'
                        + bp.target_queries[0] + '", "covered": true, "confidence": 0.9}]}'}}
        return _R()

    # Patch the IPv4 async client used inside the content-gap track.
    import aeo.utils.http as http_mod

    class _FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json, **kw):  # noqa: ANN001
            return await _fake_ollama(self, url, json, **kw)

    original = gap_mod.async_client
    gap_mod.async_client = lambda **kw: _FakeClient()  # type: ignore[assignment]
    try:
        report: GapAnalysisReport = asyncio.run(
            evaluate_gaps(page, bp, engine_target=EngineTarget.PERPLEXITY)
        )
    finally:
        gap_mod.async_client = original

    assert isinstance(report, GapAnalysisReport)
    assert report.engine_target is EngineTarget.PERPLEXITY
    tracks = {t.track for t in report.tracks}
    assert tracks == {"content_gap", "citation_gap", "freshness_gap", "entity_coverage_gap"}
    assert 0.0 <= report.aggregate_score <= 1.0
    # citation source present in links → citation gap fully covered
    citation = next(t for t in report.tracks if t.track == "citation_gap")
    assert citation.score == 1.0
    _ = http_mod  # silence unused
    print(f"  [Block 3] 4-track fan-out (perplexity) OK — aggregate={report.aggregate_score}")


def test_block4_auditor() -> None:
    rec = Recommendation(
        domain="securin.io",
        url="https://securin.io/ctem",
        type="citation",
        priority="high",
        title="Cite the CISA KEV catalog",
        description="Reference https://www.cisa.gov/known-exploited-vulnerabilities-catalog and "
                    "a clearly fake source http://not a url here",
        implementation="Add an authoritative citation to https://nvd.nist.gov/vuln-metrics/cvss",
        impact_estimate=0.4,
    )

    urls = extract_citation_urls(rec)
    assert "https://www.cisa.gov/known-exploited-vulnerabilities-catalog" in urls

    # citation signals (no network HEAD): structural validity only
    signals = asyncio.run(check_citation_signals(rec, verify_reachability=False))
    assert any(s.structurally_valid for s in signals)

    # mocked adversarial verdict → no real Ollama call
    async def _mock_auditor(r: Recommendation, page) -> tuple[bool, str | None]:  # noqa: ANN001
        return True, None

    report: AuditReport = asyncio.run(
        audit_recommendation(rec, page=None, auditor=_mock_auditor, verify_reachability=False)
    )
    assert isinstance(report, AuditReport)
    assert report.recommendation_id == rec.id
    assert report.retry_attempts == 1
    assert isinstance(report.passed, bool)
    print(f"  [Block 4] Independent auditor OK — passed={report.passed}, "
          f"citations={len(report.citation_signals)}")


def main() -> int:
    print("AEO v4 smoke test")
    test_block1_models()
    test_block2_sha256_gate()
    test_block3_fanout_perplexity()
    test_block4_auditor()
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
