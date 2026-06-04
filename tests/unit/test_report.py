"""
Unit tests for the Report block (Task G) — all DB-free.

  * the pure ``build_report`` assembler (sections, review-status mapping,
    recommendation ordering, summary);
  * the ``render_report`` text renderer (dataclass and DB-row inputs);
  * the ``aeo report`` CLI (typer CliRunner + monkeypatched reports repo).
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from aeo.cli import app
from aeo.processor import CriterionGap, GapResult
from aeo.recommender.models import CONTENT, SCHEMA, Recommendation
from aeo.report import (
    REVIEW_APPROVED,
    REVIEW_COULD_NOT_IMPROVE,
    REVIEW_PENDING,
    PageReport,
    build_report,
    persist_report,
    render_report,
)
from aeo.storage.models import CriterionScore, PageScore, Target
from aeo.validation import (
    STATUS_COULD_NOT_IMPROVE,
    STATUS_IMPROVED,
    STATUS_NO_ACTION,
    ValidationOutcome,
)

runner = CliRunner()


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def make_score(*, page_id=1, run_id=7, total=18, max_possible=50, priority="high") -> PageScore:
    criteria = {
        "schema_markup": CriterionScore("schema_markup", 1, notes="No structured data found"),
        "qa_blocks": CriterionScore("qa_blocks", 2, notes="1 Q&A pair"),
        "load_speed": CriterionScore("load_speed", 3, notes="PageSpeed unavailable", scored_by="deterministic"),
        "content_depth": CriterionScore("content_depth", 4, notes="900 words"),
    }
    return PageScore(
        page_id=page_id,
        run_id=run_id,
        criteria=criteria,
        total=total,
        max_possible=max_possible,
        priority_tier=priority,
    )


def make_gap(specs, *, page_id=1, run_id=7) -> GapResult:
    rows = [
        CriterionGap(
            criterion=c, actual=a, target=t, bestpractice_gap=max(0, t - a),
            competitor=None, competitor_gap=0, weight=1.0, priority=prio,
        )
        for (c, a, t, prio) in specs
    ]
    return GapResult(
        page_id=page_id, run_id=run_id, bestpractice_gap=0.4,
        competitor_gap=None, overall_gap=0.4, criterion_gaps=rows, intent="informational",
    )


def schema_rec() -> Recommendation:
    return Recommendation(
        rec_type=SCHEMA, criterion="schema_markup", title="Add FAQPage JSON-LD",
        rationale="Body has Q&A but no FAQPage schema.",
        payload={"schema_type": "FAQPage", "jsonld": {"@type": "FAQPage"}},
    )


def content_rec(criterion="qa_blocks") -> Recommendation:
    return Recommendation(
        rec_type=CONTENT, criterion=criterion, title=f"Improve {criterion}",
        rationale="r", payload={"edits": ["Add 'What is CTEM?' Q&A", "Add 'How does it work?' Q&A"]},
    )


def make_validation(status=STATUS_IMPROVED, *, before=18, after=27, attempts=1, recs=None) -> ValidationOutcome:
    return ValidationOutcome(
        page_id=1, run_id=7, status=status, improved=(status == STATUS_IMPROVED),
        attempts=attempts, score_before=before, score_after=after,
        review_status="needs_review", recommendations=recs or [],
    )


def make_row(report: PageReport) -> dict:
    """Shape a built report like a page_reports DB row (what for_run returns)."""
    return {
        "id": 1,
        "page_id": report.page_id,
        "run_id": report.run_id,
        "url": report.url,
        "summary": report.summary,
        "sections": report.sections,
        "review_status": report.review_status,
    }


# ---------------------------------------------------------------------------
# TestBuildReport
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_overview_and_scores_weakest_first(self):
        score = make_score()
        rep = build_report(url="https://securin.io/blog/x", score=score, page_type="blog", intent="informational")
        ov = rep.sections["overview"]
        assert ov["total"] == 18
        assert ov["max_possible"] == 50
        assert ov["score_pct"] == 36.0
        assert ov["priority_tier"] == "high"
        assert ov["page_type"] == "blog"
        values = [s["value"] for s in rep.sections["scores"]]
        assert values == sorted(values)  # weakest first
        assert rep.sections["scores"][0]["criterion"] == "schema_markup"

    def test_no_validation_defaults_to_pending(self):
        rep = build_report(url="https://x.io/p", score=make_score())
        assert rep.review_status == REVIEW_PENDING
        assert rep.sections["validation"] is None

    def test_improved_validation_is_pending_with_action(self):
        v = make_validation(STATUS_IMPROVED, before=18, after=27)
        rep = build_report(url="https://x.io/p", score=make_score(), validation=v)
        assert rep.review_status == REVIEW_PENDING
        assert rep.sections["validation"]["delta"] == 9
        assert rep.sections["human_review"]["action_required"] is True
        assert "Validated" in rep.summary

    def test_could_not_improve_maps_to_review_flag(self):
        v = make_validation(STATUS_COULD_NOT_IMPROVE, before=18, after=18, attempts=3)
        rep = build_report(url="https://x.io/p", score=make_score(), validation=v)
        assert rep.review_status == REVIEW_COULD_NOT_IMPROVE
        hr = rep.sections["human_review"]
        assert hr["action_required"] is True
        assert "3 attempt" in hr["reason"]
        assert "could not raise" in rep.summary.lower()

    def test_no_action_maps_to_approved(self):
        v = make_validation(STATUS_NO_ACTION, before=44, after=44, attempts=0)
        rep = build_report(url="https://x.io/p", score=make_score(total=44), validation=v)
        assert rep.review_status == REVIEW_APPROVED
        assert rep.sections["human_review"]["action_required"] is False

    def test_recommendations_ordered_by_gap_priority(self):
        # gap priority: qa_blocks (high) before schema_markup (low)
        gap = make_gap([("qa_blocks", 1, 4, 3.0), ("schema_markup", 1, 4, 1.0)])
        recs = [schema_rec(), content_rec("qa_blocks")]  # schema first in input
        rep = build_report(url="https://x.io/p", score=make_score(), gap=gap, recommendations=recs)
        ordered = [r["criterion"] for r in rep.sections["recommendations"]]
        assert ordered == ["qa_blocks", "schema_markup"]

    def test_gap_section_none_and_summary_counts(self):
        rep = build_report(url="https://x.io/p", score=make_score())
        assert rep.sections["gap"] is None
        assert "0 deficiencies" in rep.summary

    def test_gap_section_populated(self):
        gap = make_gap([("schema_markup", 1, 4, 3.0)])
        rep = build_report(url="https://x.io/p", score=make_score(), gap=gap)
        g = rep.sections["gap"]
        assert g["overall_gap"] == 0.4
        assert g["deficiencies"][0]["criterion"] == "schema_markup"
        assert "1 deficiency" in rep.summary

    def test_recs_default_to_validation_recommendations(self):
        v = make_validation(STATUS_IMPROVED, recs=[schema_rec()])
        rep = build_report(url="https://x.io/p", score=make_score(), validation=v)
        assert len(rep.sections["recommendations"]) == 1
        assert rep.sections["recommendations"][0]["type"] == SCHEMA

    def test_persist_report_calls_repo(self, monkeypatch):
        captured = {}

        def fake_put(page_id, run_id, summary, sections, *, review_status="pending"):
            captured.update(
                page_id=page_id, run_id=run_id, summary=summary,
                sections=sections, review_status=review_status,
            )
            return 99

        monkeypatch.setattr("aeo.storage.repos.reports.put", fake_put)
        rep = build_report(url="https://x.io/p", score=make_score(), validation=make_validation())
        rid = persist_report(rep)
        assert rid == 99
        assert captured["page_id"] == 1
        assert captured["run_id"] == 7
        assert captured["review_status"] == REVIEW_PENDING
        assert "overview" in captured["sections"]


# ---------------------------------------------------------------------------
# TestRenderReport
# ---------------------------------------------------------------------------


class TestRenderReport:
    def test_render_contains_key_sections(self):
        gap = make_gap([("schema_markup", 1, 4, 3.0)])
        v = make_validation(STATUS_IMPROVED, before=18, after=27)
        rep = build_report(
            url="https://securin.io/blog/x", score=make_score(), gap=gap,
            validation=v, recommendations=[schema_rec(), content_rec()], page_type="blog",
        )
        text = render_report(rep)
        assert "https://securin.io/blog/x" in text
        assert "OVERVIEW" in text
        assert "CRITERION SCORES" in text
        assert "GAP ANALYSIS" in text
        assert "RECOMMENDATIONS" in text
        assert "VALIDATION" in text
        assert "HUMAN REVIEW" in text
        assert "18 -> 27" in text

    def test_render_from_db_row_dict(self):
        rep = build_report(url="https://x.io/p", score=make_score(), validation=make_validation())
        row = make_row(rep)
        text = render_report(row)
        assert "https://x.io/p" in text
        assert "HUMAN REVIEW" in text

    def test_render_minimal_no_gap_no_validation(self):
        rep = build_report(url="https://x.io/p", score=make_score())
        text = render_report(rep)
        assert "OVERVIEW" in text
        assert "GAP ANALYSIS" not in text   # omitted when absent
        assert "VALIDATION" not in text
        assert "HUMAN REVIEW" in text

    def test_render_surfaces_edits_and_schema(self):
        rep = build_report(
            url="https://x.io/p", score=make_score(),
            recommendations=[schema_rec(), content_rec()],
        )
        text = render_report(rep)
        assert "Add FAQPage JSON-LD" in text
        assert "What is CTEM?" in text  # concrete edit surfaced


# ---------------------------------------------------------------------------
# TestReportCLI
# ---------------------------------------------------------------------------


class TestReportCLI:
    def _row(self):
        rep = build_report(
            url="https://securin.io/blog/x", score=make_score(),
            validation=make_validation(STATUS_IMPROVED), recommendations=[schema_rec()],
        )
        return make_row(rep)

    def test_report_by_run_id(self, monkeypatch):
        monkeypatch.setattr("aeo.storage.repos.reports.for_run", lambda run_id: [self._row()])
        result = runner.invoke(app, ["report", "--run-id", "7"])
        assert result.exit_code == 0
        assert "https://securin.io/blog/x" in result.stdout
        assert "1 report(s)" in result.stdout

    def test_report_by_target(self, monkeypatch):
        monkeypatch.setattr(
            "aeo.storage.repos.targets.find",
            lambda name: Target(id=1, name="Securin", domain="securin.io", kind="client"),
        )
        captured = {}

        def fake_for_target(target_id, kind, *, run_id=None):
            captured.update(target_id=target_id, kind=kind, run_id=run_id)
            return [self._row()]

        monkeypatch.setattr("aeo.storage.repos.reports.for_target", fake_for_target)
        result = runner.invoke(app, ["report", "Securin"])
        assert result.exit_code == 0
        assert captured == {"target_id": 1, "kind": "client", "run_id": None}
        assert "AEO/SEO REPORT" in result.stdout

    def test_report_by_page_id(self, monkeypatch):
        monkeypatch.setattr("aeo.storage.repos.reports.for_page", lambda page_id: [self._row()])
        result = runner.invoke(app, ["report", "--page-id", "5"])
        assert result.exit_code == 0
        assert "HUMAN REVIEW" in result.stdout

    def test_report_json_output(self, monkeypatch):
        monkeypatch.setattr("aeo.storage.repos.reports.for_run", lambda run_id: [self._row()])
        result = runner.invoke(app, ["report", "--run-id", "7", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert data[0]["review_status"] == REVIEW_PENDING

    def test_report_no_scope_is_error(self):
        result = runner.invoke(app, ["report"])
        assert result.exit_code != 0

    def test_report_empty_scope(self, monkeypatch):
        monkeypatch.setattr("aeo.storage.repos.reports.for_run", lambda run_id: [])
        result = runner.invoke(app, ["report", "--run-id", "7"])
        assert result.exit_code == 0
        assert "no reports found" in result.stdout
