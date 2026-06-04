"""
Unit tests for the v4 Reference Architecture Generator (L1/L2/L3), all offline.

  * framework (L2) loads the closed-vocab guardrail + criteria definitions;
  * competitor patterns (L1) aggregate structural signals from extraction bundles;
  * the generator (L3) builds a deterministic blueprint, refines priorities from
    the competitor floor, and — when an LLM is present — augments within the
    guardrail (dropping out-of-vocab / duplicate proposals) or falls back cleanly.
"""

from __future__ import annotations

from aeo.reference.competitor_patterns import extract_patterns
from aeo.reference.framework import load_framework
from aeo.reference.generator import generate_blueprint
from aeo.storage.models import ExtractionBundle


def comp_bundle(page_id, *, schema=None, entities_text="", headings=None, words=0) -> ExtractionBundle:
    by_level = {"h1": [], "h2": [], "h3": []}
    sequence = []
    for tag, text in (headings or []):
        by_level.setdefault(tag, []).append(text)
        sequence.append({"tag": tag, "text": text, "is_question": text.strip().endswith("?")})
    return ExtractionBundle(
        page_id=page_id,
        data={
            "meta": {"title": entities_text},
            "headings": {"by_level": by_level, "sequence": sequence},
            "schema_jsonld": {"types": schema or []},
            "readability": {"word_count": words},
            "chunker": {"chunks": [{"text": entities_text}]},
        },
    )


class FakeLLM:
    def __init__(self, payload, *, enabled=True, model="gemini-2.0-flash", raises=False):
        self._payload = payload
        self._enabled = enabled
        self.model = model
        self._raises = raises
        self.calls = 0

    @property
    def enabled(self):
        return self._enabled

    def generate_json(self, prompt, system=None):
        self.calls += 1
        if self._raises:
            raise RuntimeError("synthesis boom")
        return self._payload


class TestFramework:
    def test_loads_topic_and_clusters(self):
        fw = load_framework()
        assert fw.topic == "PEV"
        assert fw.version == "1"
        names = {c.name for c in fw.clusters}
        assert "ctem" in names
        # pillar slug is first in the cluster
        ctem = next(c for c in fw.clusters if c.name == "ctem")
        assert ctem.pillar_slug == "/what-is-ctem"
        assert ctem.min_pages == 10

    def test_criteria_definitions_present(self):
        fw = load_framework()
        sm = fw.criteria_definition("schema_markup")
        assert sm is not None and sm.target == 4
        assert "FAQPage" in sm.schema_org

    def test_nodes_are_validated_sitemap_nodes(self):
        fw = load_framework()
        slugs = {n.slug for n in fw.nodes}
        assert "/what-is-ctem" in slugs and "/platform" in slugs
        # required entities are filtered to the allowed vocabulary
        for n in fw.nodes:
            assert set(n.required_entities).issubset(set(fw.required_entities))


class TestCompetitorPatterns:
    def test_aggregates_signals(self):
        fw = load_framework()
        pages = [
            ("https://pentera.io/platform", comp_bundle(1, schema=["Product"], words=900,
                                                        entities_text="CTEM and BAS platform")),
            ("https://cymulate.com/blog/ctem", comp_bundle(2, schema=["TechArticle", "FAQPage"], words=1500,
                                                           entities_text="What is CTEM? MITRE ATT&CK",
                                                           headings=[("h2", "What is CTEM?")])),
        ]
        pat = extract_patterns(pages, allowed_entities=fw.required_entities,
                               domains=["pentera.io", "cymulate.com"])
        assert pat.page_count == 2
        assert pat.schema_type_counts.get("TechArticle") == 1
        assert pat.entity_coverage.get("CTEM") == 2  # both pages mention CTEM
        assert pat.entity_coverage.get("BAS") == 1
        assert "What is CTEM?" in pat.common_question_headings
        assert pat.page_type_share(next(iter(pat.page_type_counts))) > 0


class TestGeneratorDeterministic:
    def test_builds_valid_blueprint_without_llm(self):
        bp = generate_blueprint(llm=None)
        assert bp.topic == "PEV"
        assert bp.generator == "deterministic"
        assert bp.content_hash
        assert bp.node_for_slug("/what-is-ctem") is not None
        # coverage clusters carried through with min_pages
        assert any(c.min_pages == 10 for c in bp.coverage.clusters)

    def test_priority_refined_by_competitor_floor(self):
        fw = load_framework()
        # All competitor pages are pillars → pillar page-type share = 1.0 → bump.
        pages = [(f"https://x.io/guide-{i}", comp_bundle(i, words=1200)) for i in range(5)]
        pat = extract_patterns(pages, allowed_entities=fw.required_entities, domains=["x.io"])
        base = generate_blueprint(llm=None)
        refined = generate_blueprint(patterns=pat, llm=None)
        b_pillar = base.node_for_slug("/what-is-ctem").priority
        r_pillar = refined.node_for_slug("/what-is-ctem").priority
        assert r_pillar > b_pillar
        assert refined.competitors == ["x.io"]


class TestGeneratorLLMAugment:
    def test_augment_adds_valid_nodes_and_drops_invalid(self):
        payload = {
            "extra_seed_questions": {"/what-is-ctem": ["How long does CTEM take?"]},
            "augment_nodes": [
                {"slug": "/ctem-checklist", "title": "CTEM Checklist", "page_type": "pillar",
                 "intent": "informational", "journey_stage": "consideration",
                 "required_entities": ["CTEM"], "cluster": "ctem"},
                {"slug": "/bad", "title": "Bad", "page_type": "megapage"},          # invalid vocab → drop
                {"slug": "/what-is-ctem", "title": "Dup", "page_type": "pillar"},    # duplicate slug → drop
            ],
        }
        llm = FakeLLM(payload)
        bp = generate_blueprint(llm=llm)
        assert llm.calls == 1
        assert bp.generator == "gemini-2.0-flash"
        assert bp.node_for_slug("/ctem-checklist") is not None
        assert bp.node_for_slug("/bad") is None
        # extra seed question merged onto the existing node
        q = bp.node_for_slug("/what-is-ctem").seed_questions
        assert "How long does CTEM take?" in q

    def test_augment_filters_out_of_vocab_entities(self):
        payload = {"augment_nodes": [
            {"slug": "/new", "title": "New", "page_type": "pillar",
             "required_entities": ["CTEM", "Bitcoin"]},  # Bitcoin not in topic vocab
        ]}
        bp = generate_blueprint(llm=FakeLLM(payload))
        node = bp.node_for_slug("/new")
        assert node is not None
        assert node.required_entities == ["CTEM"]

    def test_llm_failure_falls_back_to_deterministic(self):
        bp = generate_blueprint(llm=FakeLLM(None, raises=True))
        assert bp.generator == "deterministic"
        assert bp.node_for_slug("/what-is-ctem") is not None

    def test_llm_disabled_is_deterministic(self):
        bp = generate_blueprint(llm=FakeLLM({"augment_nodes": []}, enabled=False))
        assert bp.generator == "deterministic"

    def test_empty_augmentation_stays_deterministic(self):
        bp = generate_blueprint(llm=FakeLLM({"augment_nodes": []}))
        assert bp.generator == "deterministic"


class TestSeedQuestionEnrichment:
    """Fix: LLM extra_seed_questions must be re-validated (dedupe/blank-strip) and
    bounded (per-slug count + string length), never merged raw via model_copy."""

    def test_seed_questions_revalidated_and_bounded(self):
        from aeo.reference.generator import _MAX_QUESTION_LEN, _MAX_QUESTIONS_PER_SLUG

        payload = {"extra_seed_questions": {"/what-is-ctem": [
            "Q dup", "Q dup",        # duplicate → collapses to one
            "   ", "",               # whitespace / blank → dropped
            "L" * 500,               # index 4 → truncated to _MAX_QUESTION_LEN
            "BEYOND THE PER-SLUG CAP",  # index 5 → dropped by the per-slug cap
        ]}}
        bp = generate_blueprint(llm=FakeLLM(payload))
        node = bp.node_for_slug("/what-is-ctem")
        qs = node.seed_questions

        assert qs.count("Q dup") == 1                      # deduped
        assert all(q.strip() for q in qs)                  # no blank/whitespace entries
        assert "L" * _MAX_QUESTION_LEN in qs               # truncated, present
        assert "L" * 500 not in qs                         # original over-long absent
        assert "BEYOND THE PER-SLUG CAP" not in qs         # per-slug cap enforced
        assert _MAX_QUESTIONS_PER_SLUG == 5

    def test_real_enrichment_relabels_as_llm_authored(self):
        payload = {"extra_seed_questions": {"/what-is-ctem": ["A genuinely new question?"]}}
        bp = generate_blueprint(llm=FakeLLM(payload))
        assert bp.generator == "gemini-2.0-flash"
        assert "A genuinely new question?" in bp.node_for_slug("/what-is-ctem").seed_questions


class TestProvenanceGuard:
    """Fix: an empty-but-present extra_seed_questions (or one that resolves nothing)
    must NOT relabel the blueprint as LLM-authored."""

    def test_empty_dict_stays_deterministic(self):
        bp = generate_blueprint(llm=FakeLLM({"extra_seed_questions": {}, "augment_nodes": []}))
        assert bp.generator == "deterministic"

    def test_unresolvable_slug_stays_deterministic(self):
        # slug doesn't match any node and no augment nodes → nothing usable added.
        bp = generate_blueprint(llm=FakeLLM({"extra_seed_questions": {"/no-such-page": ["q?"]}}))
        assert bp.generator == "deterministic"


class TestPromptVocabulary:
    """Fix: the synthesis prompt must advertise the contract's closed Literals, not
    operator-config values the validator would later reject and silently drop."""

    def test_prompt_vocab_sourced_from_contract_literals(self):
        from dataclasses import replace
        from typing import get_args

        from aeo.reference.blueprint import Intent, JourneyStage, PageType
        from aeo.reference.framework import load_framework
        from aeo.reference.generator import _deterministic_blueprint, _synthesis_prompt

        fw = load_framework()
        # Config advertises a journey_stage the JourneyStage Literal does NOT allow.
        rogue = replace(fw, journey_stages=[*fw.journey_stages, "retention"])
        base = _deterministic_blueprint("PEV", rogue, None)
        prompt = _synthesis_prompt(base, rogue, None)

        for value in (*get_args(PageType), *get_args(Intent), *get_args(JourneyStage)):
            assert value in prompt
        assert "retention" not in prompt  # config-only stage is not advertised


class TestEngineTargetRouting:
    """Ported idea: engine_target routes the synthesis PROMPT emphasis only; the
    deterministic floor is untouched and unknown targets fall back to generic."""

    def test_emphasis_injected_per_engine(self):
        from aeo.reference.framework import load_framework
        from aeo.reference.generator import _deterministic_blueprint, _synthesis_prompt

        fw = load_framework()
        base = _deterministic_blueprint("PEV", fw, None)
        assert "CITATION DENSITY" in _synthesis_prompt(base, fw, None, "perplexity")
        assert "CONVERSATIONAL COVERAGE" in _synthesis_prompt(base, fw, None, "chatgpt_search")
        assert "STRUCTURED ENTITY" in _synthesis_prompt(base, fw, None, "gemini")

    def test_unknown_engine_falls_back_to_generic(self):
        from aeo.reference.framework import load_framework
        from aeo.reference.generator import (
            _ENGINE_EMPHASIS,
            _deterministic_blueprint,
            _synthesis_prompt,
        )

        fw = load_framework()
        base = _deterministic_blueprint("PEV", fw, None)
        prompt = _synthesis_prompt(base, fw, None, "nonsense-engine")
        assert _ENGINE_EMPHASIS["generic"] in prompt

    def test_deterministic_floor_unchanged_by_engine_target(self):
        # No LLM → engine_target has no effect on the produced blueprint.
        a = generate_blueprint(llm=None, engine_target="perplexity")
        b = generate_blueprint(llm=None, engine_target="gemini")
        assert a.content_hash == b.content_hash

    def test_engine_target_threads_into_llm_prompt(self):
        captured: dict[str, str] = {}

        class CapLLM(FakeLLM):
            def generate_json(self, prompt, system=None):
                captured["prompt"] = prompt
                return super().generate_json(prompt, system)

        generate_blueprint(llm=CapLLM({"augment_nodes": []}), engine_target="perplexity")
        assert "CITATION DENSITY" in captured.get("prompt", "")


class TestVersioningHash:
    def test_same_inputs_same_hash(self):
        a = generate_blueprint(llm=None)
        b = generate_blueprint(llm=None)
        assert a.content_hash == b.content_hash

    def test_version_carried(self):
        bp = generate_blueprint(llm=None, version=7)
        assert bp.version == 7
