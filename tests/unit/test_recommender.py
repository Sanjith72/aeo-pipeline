"""
Recommender tests — schema (deterministic JSON-LD), content + entity (LLM with
deterministic fallback), and the orchestration that routes a GapResult.

No DB, no network. The LLM is a hand-rolled fake exposing the three members the
generators touch (``enabled``, ``model``, ``generate_json``); the deterministic
paths run with ``llm=None``. ``persist`` is exercised against a monkeypatched
recommendations repo so the routing/payload contract is checked without Postgres.
"""

from __future__ import annotations

from aeo.processor import CriterionGap, GapResult
from aeo.recommender import (
    CONTENT,
    ENTITY,
    SCHEMA,
    Recommendation,
    persist,
    recommend,
    recommend_content,
    recommend_entity,
    recommend_schema,
)
from aeo.recommender.content import CONTENT_CRITERIA
from aeo.reference import PageArchitecture, Reference, load_reference
from aeo.storage.models import ExtractionBundle

# --- helpers ---------------------------------------------------------------


def make_bundle(**parts) -> ExtractionBundle:
    return ExtractionBundle(page_id=1, data=dict(parts))


def reference_with_arch(page_type: str, word_count: int) -> Reference:
    """Real reference with one architecture entry overridden for a known word goal."""
    base = load_reference()
    return Reference(
        targets=base.targets,
        architecture={
            **base.architecture,
            page_type: PageArchitecture(page_type, target_word_count=word_count),
        },
        intent=base.intent,
    )


def gap_with(*criteria: str) -> GapResult:
    """A GapResult whose only meaningful content is the deficient criterion names."""
    rows = [
        CriterionGap(
            criterion=c,
            actual=1,
            target=4,
            bestpractice_gap=3,
            competitor=None,
            competitor_gap=0,
            weight=1.0,
            priority=3.0,
        )
        for c in criteria
    ]
    return GapResult(
        page_id=1,
        run_id=1,
        bestpractice_gap=0.5,
        competitor_gap=None,
        overall_gap=0.5,
        criterion_gaps=rows,
    )


class FakeLLM:
    """Minimal stand-in for LLMClient: records calls, returns a canned object."""

    def __init__(self, payload, *, enabled: bool = True, model: str = "fake-model"):
        self._payload = payload
        self.enabled = enabled
        self.model = model
        self.calls: list[tuple[str, str | None]] = []

    def generate_json(self, prompt: str, system: str | None = None):
        self.calls.append((prompt, system))
        return self._payload


# --- schema generator (deterministic) --------------------------------------


class TestSchemaRecommender:
    def test_faq_page_proposed_from_qa_pairs(self):
        bundle = make_bundle(
            schema_jsonld={"types": []},
            qa_blocks={
                "qa_pairs": [{"question": "What is CTEM?", "answer_preview": "A program."}]
            },
        )
        recs = recommend_schema(bundle, url="https://securin.io/faq")
        types = [r.payload["schema_type"] for r in recs]
        assert "FAQPage" in types
        faq = next(r for r in recs if r.payload["schema_type"] == "FAQPage")
        entity = faq.payload["jsonld"]["mainEntity"][0]
        assert entity["name"] == "What is CTEM?"
        assert entity["acceptedAnswer"]["text"] == "A program."

    def test_faq_page_skipped_when_already_present(self):
        bundle = make_bundle(
            schema_jsonld={"types": ["FAQPage"]},
            qa_blocks={"qa_pairs": [{"question": "Q?", "answer_preview": "A."}]},
        )
        recs = recommend_schema(bundle, url="https://securin.io/faq")
        assert recs == []  # single-segment URL also rules out a breadcrumb

    def test_article_proposed_from_byline(self):
        bundle = make_bundle(
            schema_jsonld={"types": []},
            eeat={"author": "Jane Doe", "publish_date": "2026-01-15"},
            meta={"title": "Threat Report", "description": "Yearly findings."},
        )
        recs = recommend_schema(bundle, url="https://securin.io/report")
        article = next(r for r in recs if r.payload["schema_type"] == "Article")
        jsonld = article.payload["jsonld"]
        assert jsonld["author"]["name"] == "Jane Doe"
        assert jsonld["datePublished"] == "2026-01-15"
        assert jsonld["headline"] == "Threat Report"

    def test_article_skipped_without_byline(self):
        bundle = make_bundle(schema_jsonld={"types": []}, eeat={}, meta={"title": "X"})
        recs = recommend_schema(bundle, url="https://securin.io/page")
        assert all(r.payload["schema_type"] != "Article" for r in recs)

    def test_organization_from_primary_entity(self):
        bundle = make_bundle(
            schema_jsonld={"types": []},
            entities={"primary": {"name": "Securin"}},
        )
        recs = recommend_schema(bundle, url="https://securin.io/page")
        org = next(r for r in recs if r.payload["schema_type"] == "Organization")
        assert org.payload["jsonld"]["name"] == "Securin"
        assert org.payload["jsonld"]["url"] == "https://securin.io"

    def test_breadcrumb_from_path_hierarchy(self):
        bundle = make_bundle(schema_jsonld={"types": []})
        recs = recommend_schema(bundle, url="https://securin.io/resources/guides/ctem")
        crumb = next(r for r in recs if r.payload["schema_type"] == "BreadcrumbList")
        items = crumb.payload["jsonld"]["itemListElement"]
        assert items[0]["name"] == "Home"
        assert len(items) == 4  # Home + 3 path segments

    def test_nothing_when_all_present(self):
        bundle = make_bundle(
            schema_jsonld={"types": ["FAQPage", "Article", "Organization", "BreadcrumbList"]},
            qa_blocks={"qa_pairs": [{"question": "Q?", "answer_preview": "A."}]},
            eeat={"author": "Jane", "publish_date": "2026-01-01"},
            entities={"primary": {"name": "Securin"}},
        )
        recs = recommend_schema(bundle, url="https://securin.io/resources/guides/x")
        assert recs == []


# --- content generator -----------------------------------------------------


class TestContentRecommender:
    def test_deterministic_advisory_for_every_content_criterion(self):
        recs = recommend_content(make_bundle(), sorted(CONTENT_CRITERIA), llm=None)
        assert len(recs) == len(CONTENT_CRITERIA)
        assert {r.criterion for r in recs} == CONTENT_CRITERIA
        assert all(r.rec_type == CONTENT for r in recs)
        assert all(r.scored_by == "deterministic" for r in recs)
        assert all(r.payload.get("guidance") for r in recs)

    def test_ignores_criteria_owned_by_other_generators(self):
        recs = recommend_content(make_bundle(), ["schema_markup", "entity_consistency"], llm=None)
        assert recs == []

    def test_content_depth_advisory_includes_word_goal(self):
        ref = reference_with_arch("blog", 1500)
        recs = recommend_content(make_bundle(), ["content_depth"], reference=ref, page_type="blog")
        guidance = recs[0].payload["guidance"]
        assert "1500" in guidance and "blog" in guidance

    def test_llm_edit_used_when_enabled(self):
        fake = FakeLLM({"summary": "Tighten the FAQ", "edits": ["Add 3 questions", "Use H3s"]})
        recs = recommend_content(make_bundle(), ["qa_blocks"], llm=fake)
        rec = recs[0]
        assert rec.scored_by == "fake-model"
        assert rec.title == "Tighten the FAQ"
        assert rec.payload["edits"] == ["Add 3 questions", "Use H3s"]
        assert fake.calls  # the model was actually consulted

    def test_llm_falls_back_to_advisory_on_empty_response(self):
        fake = FakeLLM(None)
        recs = recommend_content(make_bundle(), ["qa_blocks"], llm=fake)
        assert recs[0].scored_by == "deterministic"
        assert recs[0].payload.get("guidance")

    def test_llm_edits_string_is_coerced_to_list(self):
        fake = FakeLLM({"edits": "one concrete edit"})
        recs = recommend_content(make_bundle(), ["qa_blocks"], llm=fake)
        assert recs[0].payload["edits"] == ["one concrete edit"]
        assert recs[0].title == "Improve qa_blocks"  # no summary key → default

    def test_technical_criteria_skip_the_llm(self):
        fake = FakeLLM({"summary": "x", "edits": ["y"]})
        recs = recommend_content(make_bundle(), ["load_speed"], llm=fake)
        assert recs[0].scored_by == "deterministic"
        assert fake.calls == []  # load_speed is advisory-only, LLM never called


# --- entity generator ------------------------------------------------------


class TestEntityRecommender:
    def test_advisory_when_entity_never_named(self):
        bundle = make_bundle(
            entities={"primary": None, "entity_count": 0, "first_person_count": 0, "ratio": None}
        )
        recs = recommend_entity(bundle, ["entity_consistency"], llm=None)
        assert recs[0].rec_type == ENTITY
        assert "never names" in recs[0].payload["guidance"]

    def test_advisory_when_first_person_dominant(self):
        bundle = make_bundle(
            entities={
                "primary": {"name": "Securin"},
                "entity_count": 2,
                "first_person_count": 8,
                "ratio": 0.25,
            }
        )
        recs = recommend_entity(bundle, ["entity_consistency"], llm=None)
        guidance = recs[0].payload["guidance"]
        assert "Replace first-person" in guidance
        assert "Securin" in guidance
        assert recs[0].payload["primary_entity"] == "Securin"

    def test_ignores_non_entity_criteria(self):
        recs = recommend_entity(make_bundle(), ["qa_blocks", "schema_markup"], llm=None)
        assert recs == []

    def test_llm_edit_used_when_enabled(self):
        bundle = make_bundle(entities={"primary": {"name": "Securin"}, "entity_count": 2,
                                       "first_person_count": 8, "ratio": 0.25})
        fake = FakeLLM({"summary": "Name the brand", "edits": ["We detect → Securin detects"]})
        recs = recommend_entity(bundle, ["entity_consistency"], llm=fake)
        rec = recs[0]
        assert rec.scored_by == "fake-model"
        assert rec.criterion == "entity_consistency"
        assert rec.payload["edits"] == ["We detect → Securin detects"]
        assert rec.payload["primary_entity"] == "Securin"

    def test_grounding_names_the_primary_entity(self):
        bundle = make_bundle(entities={"primary": {"name": "Securin"}, "entity_count": 3,
                                       "first_person_count": 9, "ratio": 0.33})
        recs = recommend_entity(bundle, ["entity_consistency"], llm=None)
        assert "Securin" in recs[0].rationale


# --- orchestration ---------------------------------------------------------


class TestRecommendOrchestration:
    def _bundle(self) -> ExtractionBundle:
        return make_bundle(
            schema_jsonld={"types": []},
            qa_blocks={"qa_pairs": [{"question": "What is X?", "answer_preview": "A thing."}]},
            entities={"primary": {"name": "Securin"}, "entity_count": 2,
                      "first_person_count": 8, "ratio": 0.25},
        )

    def test_routes_each_deficient_criterion_to_its_generator(self):
        recs = recommend(
            self._bundle(),
            gap_with("schema_markup", "entity_consistency", "qa_blocks"),
            url="https://securin.io/resources/guide",
            llm=None,
        )
        schema_types = [r.payload.get("schema_type") for r in recs if r.rec_type == SCHEMA]
        assert "FAQPage" in schema_types
        assert any(r.rec_type == ENTITY and r.criterion == "entity_consistency" for r in recs)
        assert any(r.rec_type == CONTENT and r.criterion == "qa_blocks" for r in recs)

    def test_no_schema_recs_when_schema_not_deficient(self):
        recs = recommend(
            self._bundle(),
            gap_with("qa_blocks"),
            url="https://securin.io/resources/guide",
            llm=None,
        )
        assert all(r.rec_type != SCHEMA for r in recs)
        assert any(r.rec_type == CONTENT and r.criterion == "qa_blocks" for r in recs)

    def test_empty_gap_yields_no_recommendations(self):
        recs = recommend(self._bundle(), gap_with(), url="https://securin.io/x", llm=None)
        assert recs == []


# --- persistence -----------------------------------------------------------


class TestPersist:
    def test_writes_one_row_per_rec_with_provenance(self, monkeypatch):
        calls: list[dict] = []

        def fake_create(page_id, run_id, rec_type, payload, *, criterion=None,
                        attempt=1, status="proposed", score_before=None):
            calls.append(
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
            return len(calls)

        from aeo.storage.repos import recommendations as recs_repo

        monkeypatch.setattr(recs_repo, "create", fake_create)

        recs = [
            Recommendation(rec_type=CONTENT, criterion="qa_blocks", title="t1",
                           rationale="r1", payload={"guidance": "g1"}),
            Recommendation(rec_type=ENTITY, criterion="entity_consistency", title="t2",
                           rationale="r2", payload={"guidance": "g2"}, scored_by="fake-model"),
        ]
        ids = persist(5, 9, recs, attempt=2, score_before=30.0)

        assert ids == [1, 2]
        assert len(calls) == 2
        assert calls[0]["page_id"] == 5 and calls[0]["run_id"] == 9
        assert calls[0]["criterion"] == "qa_blocks" and calls[0]["attempt"] == 2
        assert calls[0]["score_before"] == 30.0
        # to_payload() folds title/rationale/source into the JSONB body
        assert calls[1]["payload"]["title"] == "t2"
        assert calls[1]["payload"]["source"] == "fake-model"
