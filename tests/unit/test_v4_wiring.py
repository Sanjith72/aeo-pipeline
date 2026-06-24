"""
Wiring tests for the v4 surface: analyze_page's Independent-Validate step and the
new CLI commands (blueprint / coverage / site-report / refinements / audit-cycle).
All DB-free (repos + orchestrator monkeypatched), so they pin the wiring contract
without a database.
"""

from __future__ import annotations

from typer.testing import CliRunner

from aeo.cli import app
from aeo.nlp.llm import LLMClient
from aeo.pipeline import analyze_page
from aeo.reference import Blueprint, SitemapNode
from aeo.scoring import score_page
from aeo.scoring.rubric import load_rubric
from aeo.settings import LLMCfg
from aeo.storage.models import ExtractionBundle
from aeo.storage.repos.blueprints import StoredBlueprint

RUBRIC = load_rubric()
DISABLED = LLMClient(LLMCfg(enabled=False))
runner = CliRunner()


def passing_bundle(page_id=1) -> ExtractionBundle:
    return ExtractionBundle(
        page_id=page_id,
        data={
            "chunker": {"chunks": [{"text": "CTEM continuously finds and fixes exposures."}]},
            "headings": {"h1_text": "What is CTEM?"},
            "schema_jsonld": {"block_count": 2, "invalid_blocks": 0, "types": ["FAQPage"]},
            "meta": {"title": "What is CTEM?"},
        },
    )


class FakePerplexity:
    def __init__(self, cited=True):
        from aeo.nlp.perplexity import CitationProbe

        self._probe = CitationProbe(question="What is CTEM?", cited=cited,
                                    citations=["https://securin.io/what-is-ctem"],
                                    matched=["https://securin.io/what-is-ctem"])

    @property
    def enabled(self):
        return True

    def cited(self, question, *, target_url):
        return self._probe


class TestAnalyzePageIndependent:
    def test_independent_off_by_default(self):
        b = passing_bundle()
        score = score_page(b, 7, llm=DISABLED, rubric=RUBRIC)
        res = analyze_page(bundle=b, score=score, url="https://securin.io/what-is-ctem",
                           rubric=RUBRIC, llm=None, persist=False, trace=False)
        assert res.independent is None
        assert "independent_validation" not in res.report.sections

    def test_independent_runs_and_attaches_section(self):
        b = passing_bundle()
        score = score_page(b, 7, llm=DISABLED, rubric=RUBRIC)
        res = analyze_page(bundle=b, score=score, url="https://securin.io/what-is-ctem",
                           rubric=RUBRIC, llm=None, persist=False, trace=False, independent=True)
        assert res.independent is not None
        assert res.independent.passed is True
        assert "independent_validation" in res.report.sections

    def test_citation_recorded_when_persisting(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr("aeo.storage.repos.feedback.record_citation",
                            lambda **kw: recorded.update(kw) or 1)
        # persist=True also writes gap/report/recs — stub those too.
        monkeypatch.setattr("aeo.storage.repos.gaps.put", lambda **kw: 1)
        monkeypatch.setattr("aeo.storage.repos.reports.put", lambda *a, **kw: 1)
        monkeypatch.setattr("aeo.storage.repos.recommendations.create", lambda *a, **kw: 1)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_validation", lambda *a, **kw: None)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_prediction", lambda *a, **kw: None)

        b = passing_bundle()
        score = score_page(b, 7, llm=DISABLED, rubric=RUBRIC)
        analyze_page(bundle=b, score=score, url="https://securin.io/what-is-ctem",
                     rubric=RUBRIC, llm=None, persist=True, trace=False,
                     independent=True, perplexity=FakePerplexity(cited=True))
        assert recorded.get("cited") is True
        assert recorded.get("url") == "https://securin.io/what-is-ctem"


def _stored_blueprint() -> StoredBlueprint:
    bp = Blueprint(topic="PEV", version=2, generator="deterministic",
                   sitemap=[SitemapNode(slug="/what-is-ctem", title="CTEM", page_type="pillar")])
    return StoredBlueprint(id=9, blueprint=bp)


class TestCLI:
    def test_blueprint_show(self, monkeypatch):
        monkeypatch.setattr("aeo.storage.repos.blueprints.latest", lambda topic: _stored_blueprint())
        result = runner.invoke(app, ["blueprint", "show", "--topic", "PEV"])
        assert result.exit_code == 0
        assert "BLUEPRINT" in result.stdout and "/what-is-ctem" in result.stdout

    def test_blueprint_show_missing(self, monkeypatch):
        monkeypatch.setattr("aeo.storage.repos.blueprints.latest", lambda topic: None)
        result = runner.invoke(app, ["blueprint", "show", "--topic", "Nope"])
        assert result.exit_code == 0
        assert "no blueprint" in result.stdout

    def test_coverage(self, monkeypatch):
        row = {
            "coverage_pct": 50.0, "missing_count": 1, "thin_count": 1,
            "detail": {"topic": "PEV", "blueprint_version": 2,
                       "missing": [{"priority": 0.9, "page_type": "pillar", "slug": "/platform", "title": "P"}],
                       "thin_clusters": [{"cluster": "ctem", "present_count": 1, "min_pages": 10}]},
        }
        monkeypatch.setattr("aeo.storage.repos.coverage.get", lambda run_id: row)
        result = runner.invoke(app, ["coverage", "-r", "5"])
        assert result.exit_code == 0
        assert "COVERAGE" in result.stdout and "/platform" in result.stdout and "THIN" in result.stdout

    def test_site_report(self, monkeypatch):
        monkeypatch.setattr(
            "aeo.storage.repos.site_reports.for_run",
            lambda run_id: {"summary": "PEV: blueprint v2", "sections": {"overview": {"topic": "PEV"}}},
        )
        result = runner.invoke(app, ["site-report", "-r", "5"])
        assert result.exit_code == 0
        assert "SITE AEO REPORT" in result.stdout

    def test_refinements_list(self, monkeypatch):
        monkeypatch.setattr(
            "aeo.storage.repos.feedback.list_refinements",
            lambda status=None: [{"id": 1, "status": "proposed", "criterion": "stats_in_html",
                                  "current_target": 4, "proposed_target": 5, "rationale": "cited win"}],
        )
        result = runner.invoke(app, ["refinements"])
        assert result.exit_code == 0
        assert "#1" in result.stdout and "stats_in_html" in result.stdout

    def test_refinements_accept(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("aeo.storage.repos.feedback.set_refinement_status",
                            lambda rid, status: seen.update(rid=rid, status=status))
        result = runner.invoke(app, ["refinements", "--accept", "3"])
        assert result.exit_code == 0
        assert seen == {"rid": 3, "status": "accepted"}

    def test_audit_cycle_command(self, monkeypatch):
        from aeo.storage.models import Target

        monkeypatch.setattr("aeo.storage.repos.targets.find",
                            lambda name: Target(id=1, name="Securin", domain="securin.io", kind="client"))

        async def fake_audit_cycle(self, domain, *, target, label=None, max_urls=None):
            return {"run": {"run_id": 5}, "analysis": {"analyzed": 3}, "site_report_id": 11}

        monkeypatch.setattr("aeo.pipeline.orchestrator.Orchestrator.audit_cycle", fake_audit_cycle)
        result = runner.invoke(app, ["audit-cycle", "securin.io", "-t", "Securin"])
        assert result.exit_code == 0
        assert '"site_report_id": 11' in result.stdout
