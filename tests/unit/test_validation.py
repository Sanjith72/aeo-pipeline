"""
Unit tests for the Validation block (Task F).

Two layers, both DB-free:
  * the synthetic-page simulator (``apply_recommendation``) -- each applier moves
    its binding signal to the Reference target, advisory-only recs are no-ops,
    and a simulated bundle re-scores higher;
  * the ``validate_page`` loop -- improved / could-not-improve / retry-cap /
    no-action paths, and persistence (with a monkeypatched recommendations repo).
"""

from __future__ import annotations

import copy

from aeo.nlp.llm import LLMClient
from aeo.processor import CriterionGap, GapResult
from aeo.recommender.models import CONTENT, ENTITY, SCHEMA, Recommendation
from aeo.reference import load_reference
from aeo.scoring import score_page
from aeo.scoring.rubric import load_rubric
from aeo.settings import LLMCfg
from aeo.storage.models import ExtractionBundle
from aeo.validation import (
    REVIEW_NEEDED,
    REVIEW_NONE,
    STATUS_COULD_NOT_IMPROVE,
    STATUS_IMPROVED,
    STATUS_NO_ACTION,
    apply_recommendation,
    validate_page,
)
from aeo.validation.simulate import _QA_PAIRS_FOR_TIER

RUBRIC = load_rubric()
REFERENCE = load_reference()
DISABLED_LLM = LLMClient(LLMCfg(enabled=False))


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def make_bundle(page_id: int = 1, **parts) -> ExtractionBundle:
    return ExtractionBundle(page_id=page_id, data=dict(parts))


class FakeRef:
    """Reference stub exposing only ``target_for`` at a fixed tier."""

    def __init__(self, target: int) -> None:
        self._t = target

    def target_for(self, _criterion: str) -> int:
        return self._t


class FakeLLM:
    """LLM stub: records calls, returns a canned JSON object."""

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


def content_edit(criterion: str, edits=("rewrite A", "rewrite B")) -> Recommendation:
    return Recommendation(
        rec_type=CONTENT,
        criterion=criterion,
        title=f"Improve {criterion}",
        rationale="r",
        payload={"edits": list(edits)},
    )


def advisory(criterion: str) -> Recommendation:
    return Recommendation(
        rec_type=CONTENT,
        criterion=criterion,
        title=f"Improve {criterion}",
        rationale="r",
        payload={"guidance": "do the thing"},
    )


def schema_rec(stype: str = "FAQPage") -> Recommendation:
    return Recommendation(
        rec_type=SCHEMA,
        criterion="schema_markup",
        title=f"Add {stype}",
        rationale="r",
        payload={"schema_type": stype, "jsonld": {"@type": stype}},
    )


def gap_with(specs, *, page_id: int = 1, run_id: int = 7) -> GapResult:
    """specs: iterable of (criterion, actual, target[, competitor])."""
    rows: list[CriterionGap] = []
    for spec in specs:
        criterion, actual, target = spec[0], spec[1], spec[2]
        competitor = spec[3] if len(spec) > 3 else None
        bp = max(0, target - actual)
        comp_gap = max(0, (competitor or 0) - actual)
        rows.append(
            CriterionGap(
                criterion=criterion,
                actual=actual,
                target=target,
                bestpractice_gap=bp,
                competitor=competitor,
                competitor_gap=comp_gap,
                weight=1.0,
                priority=float(bp + comp_gap),
            )
        )
    return GapResult(
        page_id=page_id,
        run_id=run_id,
        bestpractice_gap=0.5,
        competitor_gap=None,
        overall_gap=0.5,
        criterion_gaps=rows,
    )


# ---------------------------------------------------------------------------
# TestSimulate — apply_recommendation appliers
# ---------------------------------------------------------------------------


class TestSimulate:
    def test_schema_applier_adds_type(self):
        b = make_bundle(schema_jsonld={"types": [], "block_count": 0})
        changed = apply_recommendation(b, schema_rec("FAQPage"), rubric=RUBRIC, reference=FakeRef(4))
        assert changed is True
        assert "FAQPage" in b.data["schema_jsonld"]["types"]
        assert b.data["schema_jsonld"]["block_count"] >= 1

    def test_schema_applier_idempotent_when_present(self):
        b = make_bundle(schema_jsonld={"types": ["FAQPage"], "block_count": 1})
        changed = apply_recommendation(b, schema_rec("FAQPage"), rubric=RUBRIC, reference=FakeRef(4))
        assert changed is False

    def test_qa_applier_bumps_pair_count_to_target_band(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        changed = apply_recommendation(b, content_edit("qa_blocks"), rubric=RUBRIC, reference=FakeRef(4))
        assert changed is True
        assert b.data["qa_blocks"]["pair_count"] == _QA_PAIRS_FOR_TIER[4]

    def test_stats_applier_bumps_count_to_target(self):
        b = make_bundle(stats={"count": 0})
        apply_recommendation(b, content_edit("stats_in_html"), rubric=RUBRIC, reference=FakeRef(4))
        assert b.data["stats"]["count"] == 6  # scoring.yaml tiers[4]

    def test_entity_applier_raises_ratio_and_count(self):
        b = make_bundle(entities={"ratio": 0.2, "entity_count": 1, "first_person_count": 5})
        rec = Recommendation(
            rec_type=ENTITY,
            criterion="entity_consistency",
            title="x",
            rationale="r",
            payload={"edits": ["name the brand"]},
        )
        changed = apply_recommendation(b, rec, rubric=RUBRIC, reference=FakeRef(4))
        assert changed is True
        assert b.data["entities"]["ratio"] >= 1.5  # tiers[4]
        assert b.data["entities"]["entity_count"] >= 1

    def test_heading_applier_sets_ratio_and_clears_penalties(self):
        b = make_bundle(headings={"h23_question_ratio": 0.0, "missing_h1": True, "template_h1": True})
        apply_recommendation(b, content_edit("heading_structure"), rubric=RUBRIC, reference=FakeRef(4))
        h = b.data["headings"]
        assert h["h23_question_ratio"] >= 0.45  # tiers[4]
        assert h["missing_h1"] is False
        assert h["template_h1"] is False

    def test_depth_applier_bumps_word_count(self):
        b = make_bundle(readability={"word_count": 50})
        apply_recommendation(b, content_edit("content_depth"), rubric=RUBRIC, reference=FakeRef(4))
        assert b.data["readability"]["word_count"] == 800

    def test_readability_applier_sets_flesch_and_segments(self):
        b = make_bundle(
            readability={"word_count": 300, "flesch_reading_ease": 10.0, "avg_sentence_length": 40},
            chunker={"chunk_count": 1},
        )
        apply_recommendation(b, content_edit("answer_readability"), rubric=RUBRIC, reference=FakeRef(4))
        r = b.data["readability"]
        assert r["flesch_reading_ease"] >= 45  # flesch_tiers[4]
        assert r["avg_sentence_length"] <= 28
        assert b.data["chunker"]["chunk_count"] >= 2

    def test_advisory_rec_is_noop(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        before = copy.deepcopy(b.data)
        changed = apply_recommendation(b, advisory("qa_blocks"), rubric=RUBRIC, reference=FakeRef(4))
        assert changed is False
        assert b.data == before

    def test_bounded_to_target_noop_when_already_at_target(self):
        b = make_bundle(qa_blocks={"pair_count": 3})  # already tier-4 band
        changed = apply_recommendation(b, content_edit("qa_blocks"), rubric=RUBRIC, reference=FakeRef(4))
        assert changed is False

    def test_unknown_criterion_is_noop(self):
        b = make_bundle()
        rec = Recommendation(
            rec_type=CONTENT, criterion="load_speed", title="x", rationale="r",
            payload={"edits": ["faster"]},
        )
        assert apply_recommendation(b, rec, rubric=RUBRIC, reference=FakeRef(4)) is False

    def test_simulated_bundle_rescores_higher(self):
        b = make_bundle(qa_blocks={"pair_count": 0}, stats={"count": 0})
        before = score_page(b, 7, llm=DISABLED_LLM, rubric=RUBRIC).total
        syn = copy.deepcopy(b)
        apply_recommendation(syn, content_edit("qa_blocks"), rubric=RUBRIC, reference=REFERENCE)
        apply_recommendation(syn, content_edit("stats_in_html"), rubric=RUBRIC, reference=REFERENCE)
        after = score_page(syn, 7, llm=DISABLED_LLM, rubric=RUBRIC).total
        assert after > before

    def test_apply_does_not_mutate_original_via_deepcopy(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        syn = copy.deepcopy(b)
        apply_recommendation(syn, content_edit("qa_blocks"), rubric=RUBRIC, reference=FakeRef(4))
        assert b.data["qa_blocks"]["pair_count"] == 0  # original untouched


# ---------------------------------------------------------------------------
# TestValidatePage — the recommend -> simulate -> re-score -> retry loop
# ---------------------------------------------------------------------------


class TestValidatePage:
    def test_improved_path_with_llm(self):
        b = make_bundle(qa_blocks={"pair_count": 0})
        gap = gap_with([("qa_blocks", 1, 4)])
        llm = FakeLLM({"summary": "Add FAQs", "edits": ["Q1?", "Q2?", "Q3?"]})
        out = validate_page(b, gap, url="https://x.io/p", llm=llm, persist=False)
        assert out.status == STATUS_IMPROVED
        assert out.improved is True
        assert out.attempts == 1
        assert out.score_after > out.score_before
        assert out.review_status == REVIEW_NEEDED
        assert out.recommendations
        assert out.rec_ids == []
        assert len(llm.calls) == 1

    def test_could_not_improve_deterministic(self):
        b = make_bundle(stats={"count": 0})
        gap = gap_with([("stats_in_html", 1, 4)])
        out = validate_page(b, gap, url="https://x.io/p", llm=None, persist=False)
        assert out.status == STATUS_COULD_NOT_IMPROVE
        assert out.improved is False
        assert out.attempts == 1  # deterministic recommender -> no point retrying
        assert out.score_after == out.score_before
        assert out.review_status == REVIEW_NEEDED
        assert out.recommendations  # advisory still proposed

    def test_retry_caps_at_max_attempts(self):
        # Competitor-only gap: page already at the target band, so concrete edits
        # bounded-to-target no-op -> the loop retries to the cap, then flags.
        b = make_bundle(qa_blocks={"pair_count": 4})
        gap = gap_with([("qa_blocks", 4, 4, 5)])
        llm = FakeLLM({"summary": "x", "edits": ["Q?"]})
        out = validate_page(b, gap, url="https://x.io/p", llm=llm, persist=False)
        assert out.status == STATUS_COULD_NOT_IMPROVE
        assert out.attempts == 3
        assert len(llm.calls) == 3

    def test_max_attempts_override(self):
        b = make_bundle(qa_blocks={"pair_count": 4})
        gap = gap_with([("qa_blocks", 4, 4, 5)])
        llm = FakeLLM({"summary": "x", "edits": ["Q?"]})
        out = validate_page(b, gap, url="https://x.io/p", llm=llm, persist=False, max_attempts=2)
        assert out.attempts == 2
        assert len(llm.calls) == 2

    def test_empty_gap_is_no_action(self):
        b = make_bundle(qa_blocks={"pair_count": 2})
        gap = gap_with([])
        out = validate_page(b, gap, url="https://x.io/p", persist=False)
        assert out.status == STATUS_NO_ACTION
        assert out.attempts == 0
        assert out.score_after == out.score_before
        assert out.review_status == REVIEW_NONE
        assert out.recommendations == []

    def test_deterministic_schema_improves_without_llm(self):
        b = make_bundle(
            qa_blocks={
                "qa_pairs": [{"question": "What is CTEM?", "answer_preview": "A program."}],
                "pair_count": 0,
            }
        )
        gap = gap_with([("schema_markup", 1, 4)])
        out = validate_page(b, gap, url="https://x.io/guide", llm=None, persist=False)
        assert out.status == STATUS_IMPROVED
        assert out.improved is True
        assert out.score_after > out.score_before
        assert any(r.rec_type == SCHEMA for r in out.recommendations)


# ---------------------------------------------------------------------------
# TestPersist — write recs + stamp validation (monkeypatched repo)
# ---------------------------------------------------------------------------


class TestPersist:
    def test_persist_writes_and_stamps(self, monkeypatch):
        created: list[dict] = []
        stamped: list[dict] = []

        def fake_create(
            page_id, run_id, rec_type, payload, *,
            criterion=None, attempt=1, status="proposed", score_before=None,
        ):
            created.append(
                {
                    "page_id": page_id,
                    "run_id": run_id,
                    "rec_type": rec_type,
                    "criterion": criterion,
                    "attempt": attempt,
                    "score_before": score_before,
                    "payload": payload,
                }
            )
            return len(created)

        def fake_set_validation(rec_id, *, status, validated, score_after=None):
            stamped.append(
                {"rec_id": rec_id, "status": status, "validated": validated, "score_after": score_after}
            )

        predicted: list[dict] = []

        def fake_set_prediction(rec_id, *, point, low, high, basis):
            predicted.append({"rec_id": rec_id, "point": point, "low": low, "high": high, "basis": basis})

        monkeypatch.setattr("aeo.storage.repos.recommendations.create", fake_create)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_validation", fake_set_validation)
        monkeypatch.setattr("aeo.storage.repos.recommendations.set_prediction", fake_set_prediction)

        b = make_bundle(
            qa_blocks={
                "qa_pairs": [{"question": "What is CTEM?", "answer_preview": "A program."}],
                "pair_count": 0,
            }
        )
        gap = gap_with([("schema_markup", 1, 4)])
        out = validate_page(b, gap, url="https://x.io/guide", llm=None, persist=True)

        assert out.status == STATUS_IMPROVED
        assert out.rec_ids == [1]
        assert len(created) == 1
        assert created[0]["criterion"] == "schema_markup"
        assert created[0]["rec_type"] == SCHEMA
        assert created[0]["score_before"] == out.score_before
        # to_payload folds title/source into the JSONB
        assert "title" in created[0]["payload"]
        assert len(stamped) == 1
        assert stamped[0]["rec_id"] == 1
        assert stamped[0]["status"] == "validated"
        assert stamped[0]["validated"] is True
        assert stamped[0]["score_after"] == out.score_after
        # Feature #2 — each rec is stamped with its predicted lift, aligned to out.predicted.
        assert len(predicted) == 1
        assert predicted[0]["rec_id"] == 1
        assert predicted[0]["basis"] == out.predicted[0].basis
        assert predicted[0]["point"] == out.predicted[0].point
        # The verified schema fix improved the page, so its predicted lift is a real estimate.
        assert out.predicted[0].basis == "simulated"
        assert out.predicted[0].point is not None and out.predicted[0].point > 0
