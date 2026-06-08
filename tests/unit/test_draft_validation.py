"""
Draft-validation tests — the Block-4 gap fix (v4+).

Generated draft content used to ship into the deliverable without ever passing
back through the validation loop: ``simulate.apply_recommendation`` only re-scored
the optimistic ``edits`` list, and full missing-page drafts went into the site
report unscored. These tests exercise the closed gap end to end:

  * the **measured-draft** path in ``simulate.py`` — a real draft is re-scored on the
    signals it actually contains (a thin draft cannot fake an improvement), while the
    edits-only path stays the fallback when no draft is present;
  * the ``validate_page`` loop driving improved / could-not-improve on draft copy;
  * ``draft_check.validate_page_draft`` — full PageDrafts through the non-circular
    Independent Validator + the citation-signal hallucination check;
  * ``draft_site_pages`` attaching that verdict to every drafted brief.

No DB, no network.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

from aeo.nlp.llm import LLMClient
from aeo.processor import CriterionGap, GapResult
from aeo.recommender.draft import draft_missing_page, draft_site_pages
from aeo.recommender.models import CONTENT, Recommendation
from aeo.reference import load_reference
from aeo.scoring.rubric import load_rubric
from aeo.settings import LLMCfg
from aeo.storage.models import ExtractionBundle
from aeo.validation import STATUS_COULD_NOT_IMPROVE, STATUS_IMPROVED, validate_page
from aeo.validation.draft_check import (
    render_page_draft_html,
    signals_from_draft,
    validate_page_draft,
)
from aeo.validation.simulate import apply_recommendation

RUBRIC = load_rubric()
REFERENCE = load_reference()
DISABLED_LLM = LLMClient(LLMCfg(enabled=False))

# A node whose title is question-shaped, so a clean draft passes h1_is_question.
NODE = {
    "slug": "/what-is-exposure-management",
    "title": "What Is Exposure Management?",
    "page_type": "pillar",
    "intent": "informational",
    "cluster": "exposure-management",
    "required_entities": ["Attack Surface", "KEV", "EPSS"],
    "seed_questions": ["What is exposure management?", "How is it different from ASM?"],
}


def make_bundle(page_id: int = 1, **parts) -> ExtractionBundle:
    return ExtractionBundle(page_id=page_id, data=dict(parts))


def draft_rec(criterion: str, draft: str, *, edits=("rewrite A",)) -> Recommendation:
    """A content rec carrying both edits and ready-to-publish draft copy."""
    payload: dict = {"edits": list(edits)}
    if draft is not None:
        payload["draft"] = draft
    return Recommendation(
        rec_type=CONTENT, criterion=criterion, title=f"Improve {criterion}",
        rationale="r", payload=payload,
    )


def gap_with(specs, *, page_id: int = 1, run_id: int = 7) -> GapResult:
    rows: list[CriterionGap] = []
    for criterion, actual, target in specs:
        bp = max(0, target - actual)
        rows.append(
            CriterionGap(
                criterion=criterion, actual=actual, target=target,
                bestpractice_gap=bp, competitor=None, competitor_gap=0,
                weight=1.0, priority=float(bp),
            )
        )
    return GapResult(
        page_id=page_id, run_id=run_id, bestpractice_gap=0.5,
        competitor_gap=None, overall_gap=0.5, criterion_gaps=rows,
    )


class FakeLLM:
    """LLM stub returning a canned JSON object (so content.py emits a draft payload)."""

    def __init__(self, payload, *, enabled: bool = True, model: str = "fake-model") -> None:
        self._payload = payload
        self._enabled = enabled
        self.model = model
        self.calls: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def generate_json(self, prompt: str, system: str | None = None) -> dict | None:
        self.calls.append({"prompt": prompt, "system": system})
        return self._payload


class FakePerplexity:
    """Citation-probe stub for the Independent Validator's real-world signal."""

    def __init__(self, *, enabled: bool = True, cited: bool = True) -> None:
        self.enabled = enabled
        self._cited = cited
        self.calls: list[tuple] = []

    def cited(self, question, *, target_url):
        self.calls.append((question, target_url))
        return SimpleNamespace(
            cited=self._cited,
            question=question,
            citations=["https://securin.io/x"] if self._cited else [],
            matched=["securin.io"] if self._cited else [],
        )


_STRONG_DEPTH_DRAFT = "## Deep dive\n\n" + ("insight " * 900)
_WEAK_DEPTH_DRAFT = "## Too thin\n\nOnly a sentence here."
_STRONG_QA_DRAFT = (
    "## What is exposure management?\n\n"
    "Exposure management is a continuous program that inventories assets and "
    "prioritizes the riskiest exposures so teams fix what matters first.\n\n"
    "## How is it different from ASM?\n\n"
    "ASM only maps the attack surface; exposure management adds scoring, validation, "
    "and remediation tracking on top so the work is prioritized end to end.\n\n"
    "## Why does it matter?\n\n"
    "Because finite security teams cannot fix everything, ranking exposures by real "
    "risk is what turns scanning output into measurable risk reduction over time.\n"
)


# ---------------------------------------------------------------------------
# TestDraftSimulate — the measured-draft path in apply_recommendation
# ---------------------------------------------------------------------------


class TestDraftSimulate:
    def test_strong_draft_measured_raises_signal(self):
        b = make_bundle(readability={"word_count": 120})
        changed = apply_recommendation(
            b, draft_rec("content_depth", _STRONG_DEPTH_DRAFT), rubric=RUBRIC, reference=REFERENCE
        )
        assert changed is True
        assert b.data["readability"]["word_count"] > 800  # measured from the real draft

    def test_weak_draft_cannot_fake_improvement(self):
        # The core guarantee: a thin draft does NOT raise the signal, so the loop
        # routes the page to retry / could-not-improve instead of claiming a win.
        b = make_bundle(readability={"word_count": 120})
        changed = apply_recommendation(
            b, draft_rec("content_depth", _WEAK_DEPTH_DRAFT), rubric=RUBRIC, reference=REFERENCE
        )
        assert changed is False
        assert b.data["readability"]["word_count"] == 120

    def test_draft_path_bypasses_optimistic_applier(self):
        # A weak draft must NOT inherit the edits applier's optimistic bump-to-target
        # (which would land word_count at the tier-4 band of 800). It stays measured.
        b = make_bundle(readability={"word_count": 120})
        apply_recommendation(
            b, draft_rec("content_depth", _WEAK_DEPTH_DRAFT), rubric=RUBRIC, reference=REFERENCE
        )
        assert b.data["readability"]["word_count"] != 800

    def test_qa_draft_measured_from_pairs(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        apply_recommendation(
            b, draft_rec("qa_blocks", _STRONG_QA_DRAFT), rubric=RUBRIC, reference=REFERENCE
        )
        assert b.data["qa_blocks"]["pair_count"] >= 3  # three real Q&A pairs in the draft

    def test_heading_draft_raises_ratio_and_clears_defects(self):
        b = make_bundle(headings={"h23_question_ratio": 0.0, "missing_h1": True, "template_h1": True})
        draft = "# What Is CTEM?\n\n## What is it?\n\nA program.\n\n## How does it work?\n\nIt scans."
        apply_recommendation(
            b, draft_rec("heading_structure", draft), rubric=RUBRIC, reference=REFERENCE
        )
        h = b.data["headings"]
        assert h["h23_question_ratio"] > 0.0
        assert h["missing_h1"] is False
        assert h["template_h1"] is False

    def test_edits_only_path_unchanged_when_no_draft(self):
        # No draft -> the optimistic applier remains the fallback (tier-4 band = 800).
        b = make_bundle(readability={"word_count": 50})
        changed = apply_recommendation(
            b, draft_rec("content_depth", None), rubric=RUBRIC, reference=REFERENCE
        )
        assert changed is True
        assert b.data["readability"]["word_count"] == 800

    def test_draft_does_not_mutate_original(self):
        b = make_bundle(readability={"word_count": 120})
        snapshot = copy.deepcopy(b.data)
        synthetic = copy.deepcopy(b)
        apply_recommendation(
            synthetic, draft_rec("content_depth", _STRONG_DEPTH_DRAFT), rubric=RUBRIC, reference=REFERENCE
        )
        assert b.data == snapshot


# ---------------------------------------------------------------------------
# TestValidatePageWithDraft — the loop measures what would ship
# ---------------------------------------------------------------------------


class TestValidatePageWithDraft:
    def test_strong_draft_drives_improved(self):
        b = make_bundle(readability={"word_count": 120})
        gap = gap_with([("content_depth", 1, 4)])
        llm = FakeLLM({"summary": "Deepen", "edits": ["expand"], "draft": _STRONG_DEPTH_DRAFT})
        out = validate_page(b, gap, url="https://x.io/p", llm=llm, persist=False)
        assert out.status == STATUS_IMPROVED
        assert out.score_after > out.score_before

    def test_weak_draft_drives_could_not_improve(self):
        b = make_bundle(readability={"word_count": 120})
        gap = gap_with([("content_depth", 1, 4)])
        llm = FakeLLM({"summary": "Deepen", "edits": ["expand"], "draft": _WEAK_DEPTH_DRAFT})
        out = validate_page(b, gap, url="https://x.io/p", llm=llm, persist=False)
        assert out.status == STATUS_COULD_NOT_IMPROVE
        assert out.score_after == out.score_before
        assert out.attempts == 3  # llm enabled -> retried to the cap, still no gain


# ---------------------------------------------------------------------------
# TestSignalsFromDraft — markdown + HTML both yield real extractor signals
# ---------------------------------------------------------------------------


class TestSignalsFromDraft:
    def test_markdown_headings_become_real_nodes(self):
        sig = signals_from_draft(_STRONG_QA_DRAFT)
        assert sig["qa_blocks"]["pair_count"] >= 3
        assert sig["headings"]["h23_question_ratio"] > 0.0

    def test_html_draft_passed_through(self):
        sig = signals_from_draft("<h2>What is it?</h2><p>" + ("word " * 90) + "</p>")
        assert sig["readability"]["word_count"] >= 80


# ---------------------------------------------------------------------------
# TestValidatePageDraft — full PageDrafts through independent + citation checks
# ---------------------------------------------------------------------------


class TestValidatePageDraft:
    def test_scaffold_draft_passes_independent(self):
        payload = draft_missing_page(NODE, topic="exposure management", llm=None, origin="securin.io").to_payload()
        verdict = validate_page_draft(payload, url="securin.io")
        assert verdict["passed"] is True
        names = {c["name"]: c["passed"] for c in verdict["independent"]["checks"]}
        assert names["valid_jsonld_present"] is True
        assert names["h1_is_question"] is True
        assert verdict["hallucinated_citations"] == 0

    def test_fabricated_citation_is_flagged(self):
        payload = draft_missing_page(NODE, topic="exposure management", llm=None, origin="securin.io").to_payload()
        payload["body_markdown"] += "\n\nSource: http://not a real url with spaces"
        verdict = validate_page_draft(payload, url="securin.io")
        assert verdict["hallucinated_citations"] >= 1
        assert verdict["passed"] is False

    def test_missing_jsonld_fails_independent(self):
        payload = draft_missing_page(NODE, topic="exposure management", llm=None, origin="securin.io").to_payload()
        payload["jsonld"] = []  # strip the code-built schema
        verdict = validate_page_draft(payload, url="securin.io")
        names = {c["name"]: c["passed"] for c in verdict["independent"]["checks"]}
        assert names["valid_jsonld_present"] is False
        assert verdict["passed"] is False

    def test_non_question_h1_fails_independent(self):
        node = dict(NODE, slug="/exposure-management", title="Exposure Management Guide")
        payload = draft_missing_page(node, topic="exposure management", llm=None, origin="securin.io").to_payload()
        verdict = validate_page_draft(payload, url="securin.io")
        names = {c["name"]: c["passed"] for c in verdict["independent"]["checks"]}
        assert names["h1_is_question"] is False
        assert verdict["passed"] is False

    def test_perplexity_citation_signal_recorded(self):
        payload = draft_missing_page(NODE, topic="exposure management", llm=None, origin="securin.io").to_payload()
        ppx = FakePerplexity(enabled=True, cited=True)
        verdict = validate_page_draft(payload, url="https://securin.io/x", perplexity=ppx)
        assert ppx.calls  # the probe actually ran
        assert verdict["independent"]["citation"]["available"] is True
        assert verdict["independent"]["citation"]["cited"] is True

    def test_render_page_draft_html_includes_jsonld(self):
        payload = draft_missing_page(NODE, topic="exposure management", llm=None, origin="securin.io").to_payload()
        html = render_page_draft_html(payload)
        assert "application/ld+json" in html
        assert "<h1>" in html or "<h2>" in html


# ---------------------------------------------------------------------------
# TestDraftSitePagesValidation — verdict attached to every drafted brief
# ---------------------------------------------------------------------------


class TestDraftSitePagesValidation:
    def test_draft_site_pages_attaches_validation(self):
        briefs = [dict(NODE)]
        out = draft_site_pages(briefs, topic="exposure management", llm=None, origin="securin.io", limit=5)
        assert "draft" in out[0]
        assert "validation" in out[0]["draft"]
        assert "passed" in out[0]["draft"]["validation"]
        assert "independent" in out[0]["draft"]["validation"]
