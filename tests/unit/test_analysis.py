"""
Unit tests for the back-half pipeline wiring (Task H) — all DB-free.

  * ``build_competitor_pool`` (pure row -> CompetitorPage transform);
  * ``analyze_page`` (Gap -> Validate -> Report for one page; improvement,
    competitor selection, could-not-improve, and persistence wiring);
  * the ANALYZE_RUN job type (enqueue + worker dispatch);
  * the ``aeo analyze`` CLI command.
"""

from __future__ import annotations

from typer.testing import CliRunner

from aeo.cli import app
from aeo.nlp.llm import LLMClient
from aeo.pipeline import (
    ANALYZE_RUN,
    Worker,
    analyze_page,
    build_competitor_pool,
    enqueue_analysis,
    is_could_not_improve,
    is_improved,
)
from aeo.pipeline.orchestrator import AnalysisSummary
from aeo.reference import load_reference
from aeo.scoring import score_page
from aeo.scoring.rubric import load_rubric
from aeo.settings import LLMCfg
from aeo.storage.models import ExtractionBundle

REFERENCE = load_reference()
RUBRIC = load_rubric()
DISABLED = LLMClient(LLMCfg(enabled=False))
runner = CliRunner()


class FakeLLM:
    def __init__(self, payload, *, enabled=True, model="fake-model"):
        self._payload = payload
        self._enabled = enabled
        self.model = model
        self.calls: list[dict] = []

    @property
    def enabled(self):
        return self._enabled

    def generate_json(self, prompt, system=None):
        self.calls.append({"prompt": prompt, "system": system})
        return self._payload


def make_bundle(page_id=1, **parts) -> ExtractionBundle:
    return ExtractionBundle(page_id=page_id, data=dict(parts))


def score_of(bundle, run_id=7):
    return score_page(bundle, run_id, llm=DISABLED, rubric=RUBRIC)


def competitor_row(page_id, url, **tiers):
    base = {
        "page_id": page_id, "url": url, "total_score": sum(tiers.values()),
        "schema_markup_score": None, "qa_blocks_score": None, "stats_in_html_score": None,
        "entity_consistency_score": None, "heading_structure_score": None,
        "content_depth_score": None, "citation_signals_score": None, "load_speed_score": None,
        "render_accessibility_score": None, "answer_readability_score": None,
    }
    for name, val in tiers.items():
        base[f"{name}_score"] = val
    return base


# ---------------------------------------------------------------------------
# TestBuildCompetitorPool
# ---------------------------------------------------------------------------


class TestBuildCompetitorPool:
    def test_builds_candidates_with_intent(self):
        rows = [competitor_row(11, "https://xmcyber.com/blog/ctem", schema_markup=4, qa_blocks=5)]
        pool = build_competitor_pool(rows, REFERENCE)
        assert len(pool) == 1
        cp = pool[0]
        assert cp.page_id == 11
        assert cp.intent == "informational"  # /blog -> informational
        assert cp.tiers["qa_blocks"] == 5
        assert cp.total == 9  # helper sets total_score = sum of tiers

    def test_skips_rows_without_tiers(self):
        rows = [competitor_row(12, "https://x.io/empty")]  # all tier cols None
        assert build_competitor_pool(rows, REFERENCE) == []


# ---------------------------------------------------------------------------
# TestAnalyzePage
# ---------------------------------------------------------------------------


class TestAnalyzePage:
    def test_runs_gap_validate_report(self):
        bundle = make_bundle(qa_blocks={"pair_count": 0}, stats={"count": 0})
        score = score_of(bundle)
        llm = FakeLLM({"summary": "Add FAQs", "edits": ["Q1?", "Q2?", "Q3?"]})
        result = analyze_page(
            bundle=bundle, score=score, url="https://securin.io/blog/ctem",
            reference=REFERENCE, rubric=RUBRIC, llm=llm, persist=False, trace=False,
        )
        assert result.gap is not None
        assert result.validation is not None
        assert result.report is not None
        assert result.intent == "informational"
        assert "overview" in result.report.sections
        assert is_improved(result) is True

    def test_competitor_selected_when_intent_matches(self):
        bundle = make_bundle(qa_blocks={"pair_count": 0})
        score = score_of(bundle)
        pool = build_competitor_pool(
            [competitor_row(99, "https://pentera.io/blog/x", schema_markup=5, qa_blocks=5,
                            stats_in_html=5, entity_consistency=5, heading_structure=5,
                            content_depth=5, citation_signals=5, load_speed=5,
                            render_accessibility=5, answer_readability=5)],
            REFERENCE,
        )
        result = analyze_page(
            bundle=bundle, score=score, url="https://securin.io/blog/ctem",
            reference=REFERENCE, rubric=RUBRIC, llm=None, competitors=pool,
            persist=False, trace=False,
        )
        assert result.gap.competitor_gap is not None
        assert result.gap.competitor_page_id == 99

    def test_no_competitor_when_pool_empty(self):
        bundle = make_bundle(qa_blocks={"pair_count": 0})
        score = score_of(bundle)
        result = analyze_page(
            bundle=bundle, score=score, url="https://securin.io/blog/ctem",
            reference=REFERENCE, rubric=RUBRIC, llm=None, competitors=[],
            persist=False, trace=False,
        )
        assert result.gap.competitor_gap is None

    def test_could_not_improve_without_llm(self):
        # No concrete artifacts: single-segment URL (no breadcrumb), no qa_pairs/
        # entities/eeat (no schema rec) -> only advisories -> nothing to apply.
        bundle = make_bundle(stats={"count": 0})
        score = score_of(bundle)
        result = analyze_page(
            bundle=bundle, score=score, url="https://securin.io/resources",
            reference=REFERENCE, rubric=RUBRIC, llm=None, persist=False, trace=False,
        )
        assert is_could_not_improve(result) is True
        assert result.report.review_status == "could_not_improve"

    def test_persists_all_three_blocks(self, monkeypatch):
        calls = {"gap": 0, "report": 0, "rec_create": 0, "rec_validate": 0}

        monkeypatch.setattr("aeo.storage.repos.gaps.put",
                            lambda **kw: calls.__setitem__("gap", calls["gap"] + 1) or 1)
        monkeypatch.setattr("aeo.storage.repos.reports.put",
                            lambda *a, **kw: calls.__setitem__("report", calls["report"] + 1) or 1)

        def fake_create(*a, **kw):
            calls["rec_create"] += 1
            return calls["rec_create"]

        def fake_set_validation(*a, **kw):
            calls["rec_validate"] += 1

        monkeypatch.setattr("aeo.storage.repos.recommendations.create", fake_create)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_validation", fake_set_validation)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_prediction", lambda *a, **kw: None)

        bundle = make_bundle(qa_blocks={"pair_count": 0})
        score = score_of(bundle)
        llm = FakeLLM({"summary": "s", "edits": ["e1", "e2"]})
        analyze_page(
            bundle=bundle, score=score, url="https://securin.io/blog/ctem",
            reference=REFERENCE, rubric=RUBRIC, llm=llm, persist=True, trace=False,
        )
        assert calls["gap"] == 1
        assert calls["report"] == 1
        assert calls["rec_create"] >= 1
        assert calls["rec_validate"] == calls["rec_create"]


# ---------------------------------------------------------------------------
# TestAnalyzeJob — enqueue + worker dispatch
# ---------------------------------------------------------------------------


class TestAnalyzeJob:
    def test_enqueue_analysis(self, monkeypatch):
        captured = {}

        def fake_enqueue(kind, payload, *, max_attempts=4):
            captured.update(kind=kind, payload=payload, max_attempts=max_attempts)
            return 42

        monkeypatch.setattr("aeo.storage.repos.jobs.enqueue", fake_enqueue)
        job_id = enqueue_analysis(5)
        assert job_id == 42
        assert captured["kind"] == ANALYZE_RUN
        assert captured["payload"] == {"run_id": 5}

    def test_worker_dispatches_analyze_run(self, monkeypatch):
        worker = Worker(kinds=[ANALYZE_RUN])
        seen = {}
        monkeypatch.setattr(worker._orch, "analyze_run", lambda run_id: seen.update(run_id=run_id))
        worker._dispatch({"kind": ANALYZE_RUN, "payload": {"run_id": 9}})
        assert seen == {"run_id": 9}


# ---------------------------------------------------------------------------
# TestAnalyzeCLI
# ---------------------------------------------------------------------------


class TestAnalyzeRun:
    """The integrative loop with repos/tracing stubbed out (DB-free)."""

    def _patch(self, monkeypatch, pages, get_fn):
        monkeypatch.setattr("aeo.storage.repos.scores.latest_competitor_scores", lambda limit=500: [])
        monkeypatch.setattr(
            "aeo.storage.repos.scores.scored_pages_for_run",
            lambda run_id, *, owner=None, limit=100_000: pages,
        )
        monkeypatch.setattr("aeo.storage.repos.extractions.get", get_fn)
        # persistence + tracing become no-ops
        monkeypatch.setattr("aeo.storage.repos.gaps.put", lambda **kw: 1)
        monkeypatch.setattr("aeo.storage.repos.reports.put", lambda *a, **kw: 1)
        monkeypatch.setattr("aeo.storage.repos.recommendations.create", lambda *a, **kw: 1)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_validation", lambda *a, **kw: None)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_prediction", lambda *a, **kw: None)
        monkeypatch.setattr("aeo.storage.repos.traces.record", lambda **kw: None)

    def test_tallies_pages(self, monkeypatch):
        from aeo.pipeline import Orchestrator

        pages = [{"page_id": 1, "url": "https://securin.io/blog/ctem"}]
        bundles = {1: make_bundle(qa_blocks={"pair_count": 0})}
        self._patch(monkeypatch, pages, lambda pid: bundles.get(pid))

        events: list[tuple[str, dict]] = []
        summary = Orchestrator(llm=DISABLED).analyze_run(
            7, progress=lambda stage, counts: events.append((stage, counts))
        )
        assert summary.total == 1
        assert summary.analyzed == 1
        assert summary.failed == 0
        # #7: analyze_run emits a stage-end progress event with counts
        assert events and events[-1][0] == "analyze"
        assert events[-1][1]["analyzed"] == 1

    def test_isolates_per_page_failure(self, monkeypatch):
        from aeo.pipeline import Orchestrator

        pages = [
            {"page_id": 1, "url": "https://securin.io/blog/ctem"},
            {"page_id": 2, "url": "https://securin.io/blog/bad"},
        ]
        bundles = {1: make_bundle(qa_blocks={"pair_count": 0})}

        def get_fn(pid):
            if pid == 2:
                raise RuntimeError("boom")  # Error Sink must catch this
            return bundles.get(pid)

        self._patch(monkeypatch, pages, get_fn)

        summary = Orchestrator(llm=DISABLED).analyze_run(7)
        assert summary.total == 2
        assert summary.analyzed == 1   # page 1 still processed
        assert summary.failed == 1     # page 2 isolated, run continued


class TestAnalyzeCLI:
    def test_analyze_command(self, monkeypatch):
        def fake_analyze_run(self, run_id, *, persist=True, limit=100_000):
            return AnalysisSummary(run_id=run_id, total=4, analyzed=4, improved=3, could_not_improve=1)

        monkeypatch.setattr("aeo.pipeline.orchestrator.Orchestrator.analyze_run", fake_analyze_run)
        result = runner.invoke(app, ["analyze", "--run-id", "7"])
        assert result.exit_code == 0
        assert '"analyzed": 4' in result.stdout
        assert '"improved": 3' in result.stdout

    def test_analyze_no_persist_flag(self, monkeypatch):
        captured = {}

        def fake_analyze_run(self, run_id, *, persist=True, limit=100_000):
            captured.update(run_id=run_id, persist=persist)
            return AnalysisSummary(run_id=run_id)

        monkeypatch.setattr("aeo.pipeline.orchestrator.Orchestrator.analyze_run", fake_analyze_run)
        result = runner.invoke(app, ["analyze", "-r", "7", "--no-persist"])
        assert result.exit_code == 0
        assert captured == {"run_id": 7, "persist": False}
