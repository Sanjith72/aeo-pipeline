"""
Content Drafter tests — ready-to-publish page generation (v4+).

Covers the deterministic scaffold (always complete), the LLM full-prose path, the
in-code JSON-LD assembly (valid + never model-authored), graceful fallback, the
bounded site-page batch, and the new ready-to-publish `draft` payload key on the
existing-page content/entity recommenders. No DB, no network.
"""

from __future__ import annotations

from aeo.processor.coverage_diff import MissingNode
from aeo.recommender import recommend_content, recommend_entity
from aeo.recommender.draft import draft_missing_page, draft_site_pages
from aeo.storage.models import ExtractionBundle

NODE = {
    "slug": "/what-is-exposure-management",
    "title": "What Is Exposure Management?",
    "page_type": "pillar",
    "intent": "informational",
    "journey_stage": "awareness",
    "cluster": "exposure-management",
    "priority": 0.9,
    "required_entities": ["Attack Surface", "KEV", "EPSS"],
    "seed_questions": ["What is exposure management?", "How is it different from ASM?"],
}


class FakeLLM:
    def __init__(self, payload, *, enabled: bool = True, model: str = "fake-model"):
        self._payload = payload
        self.enabled = enabled
        self.model = model
        self.calls: list[tuple] = []

    def generate_json(self, prompt, system=None):
        self.calls.append((prompt, system))
        return self._payload


def _full_payload():
    return {
        "meta_description": "A clear, citable guide to exposure management.",
        "intro": "Exposure management is the continuous practice of finding and reducing risk.",
        "sections": [
            {"heading": "What is exposure management?",
             "body": "Exposure management is a continuous program that inventories assets and prioritizes risk."},
            {"heading": "How is it different from ASM?",
             "body": "ASM maps the attack surface; exposure management adds prioritization and validation."},
        ],
        "faq": [{"question": "Is exposure management the same as ASM?",
                 "answer": "No. ASM is a component; exposure management is the broader program."}],
    }


class TestDeterministicScaffold:
    def test_scaffold_is_complete(self):
        d = draft_missing_page(NODE, topic="PEV", llm=None)
        assert d.generator == "deterministic"
        assert d.draft_quality == "scaffold"
        assert d.h1 == "What Is Exposure Management?"
        assert d.sections                      # has section skeleton
        assert d.faq[0]["question"] == "What is exposure management?"
        assert d.word_count > 0
        assert "# What Is Exposure Management?" in d.body_markdown

    def test_scaffold_jsonld_built_in_code(self):
        d = draft_missing_page(NODE, topic="PEV", llm=None, origin="securin.io")
        types = [b["@type"] for b in d.jsonld]
        assert "TechArticle" in types          # pillar → TechArticle
        assert "FAQPage" in types
        art = next(b for b in d.jsonld if b["@type"] == "TechArticle")
        assert art["url"] == "https://securin.io/what-is-exposure-management"
        faq = next(b for b in d.jsonld if b["@type"] == "FAQPage")
        assert faq["mainEntity"][0]["acceptedAnswer"]["@type"] == "Answer"
        # single-segment slug → no breadcrumb
        assert all(b["@type"] != "BreadcrumbList" for b in d.jsonld)

    def test_breadcrumb_for_nested_slug(self):
        node = {**NODE, "slug": "/resources/exposure-management"}
        d = draft_missing_page(node, topic="PEV", llm=None, origin="https://securin.io")
        crumb = next(b for b in d.jsonld if b["@type"] == "BreadcrumbList")
        items = crumb["itemListElement"]
        assert items[0]["name"] == "Home"
        assert len(items) == 3                 # Home + 2 path segments

    def test_accepts_missingnode_dataclass(self):
        node = MissingNode(slug="/pricing", title="Pricing", page_type="product",
                           intent="commercial", journey_stage="decision", cluster=None, priority=0.7)
        d = draft_missing_page(node, topic="Payments", llm=None)
        assert d.slug == "/pricing"
        assert d.sections
        assert "WebPage" in [b["@type"] for b in d.jsonld]   # product → WebPage, not Article


class TestLLMProse:
    def test_full_prose_used_when_enabled(self):
        fake = FakeLLM(_full_payload())
        d = draft_missing_page(NODE, topic="PEV", llm=fake)
        assert d.generator == "fake-model"
        assert d.draft_quality == "full"
        assert d.sections[0].body.startswith("Exposure management is a continuous")
        assert d.faq[0]["answer"].startswith("No. ASM is a component")
        assert fake.calls                                    # model actually consulted
        assert any(b["@type"] == "FAQPage" for b in d.jsonld)  # JSON-LD still code-built

    def test_empty_response_falls_back_to_scaffold(self):
        d = draft_missing_page(NODE, topic="PEV", llm=FakeLLM(None))
        assert d.generator == "deterministic"

    def test_no_usable_sections_falls_back(self):
        d = draft_missing_page(NODE, topic="PEV", llm=FakeLLM({"sections": []}))
        assert d.generator == "deterministic"


class TestSitePagesBatch:
    def test_bounds_to_limit(self):
        briefs = [
            {"slug": f"/p{i}", "title": f"Page {i}", "page_type": "pillar",
             "priority": 1.0, "seed_questions": [], "required_entities": []}
            for i in range(5)
        ]
        out = draft_site_pages(briefs, topic="T", llm=None, limit=2)
        assert "draft" in out[0] and "draft" in out[1]
        assert "draft" not in out[2]            # beyond limit → brief stays lightweight

    def test_zero_limit_is_noop(self):
        briefs = [{"slug": "/p", "title": "P", "page_type": "pillar"}]
        out = draft_site_pages(briefs, topic="T", llm=None, limit=0)
        assert "draft" not in out[0]


class TestExistingPageDraftKey:
    """The existing-page recommenders now emit finished copy under payload['draft']."""

    def _bundle(self) -> ExtractionBundle:
        return ExtractionBundle(page_id=1, data={})

    def test_content_emits_ready_to_publish_draft(self):
        fake = FakeLLM({"summary": "Add an FAQ", "edits": ["Add 3 questions"],
                        "draft": "## FAQ\n\n**What is X?** X is a thing."})
        recs = recommend_content(self._bundle(), ["qa_blocks"], llm=fake)
        assert recs[0].payload["draft"].startswith("## FAQ")
        assert recs[0].payload["edits"] == ["Add 3 questions"]
        # to_payload() carries the draft into the persisted JSONB
        assert recs[0].to_payload()["draft"].startswith("## FAQ")

    def test_content_without_draft_still_works(self):
        fake = FakeLLM({"edits": ["just an edit"]})
        recs = recommend_content(self._bundle(), ["qa_blocks"], llm=fake)
        assert "draft" not in recs[0].payload      # absent unless the model supplied it
        assert recs[0].payload["edits"] == ["just an edit"]

    def test_entity_emits_draft(self):
        bundle = ExtractionBundle(page_id=1, data={
            "entities": {"primary": {"name": "Securin"}, "entity_count": 2,
                         "first_person_count": 8, "ratio": 0.25}
        })
        fake = FakeLLM({"summary": "Name the brand", "edits": ["We → Securin"],
                        "draft": "Securin detects exposures across the attack surface."})
        recs = recommend_entity(bundle, ["entity_consistency"], llm=fake)
        assert recs[0].payload["draft"].startswith("Securin detects")
        assert recs[0].payload["primary_entity"] == "Securin"
