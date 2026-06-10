"""SP-3 Implementation Asset Packager — bundle assembly + materialization."""

from __future__ import annotations

import io
import json
import zipfile
from xml.etree import ElementTree as ET

from aeo.intelligence.brief import plan_from_brief
from aeo.reference.blueprint import Blueprint, CoverageCluster, CoverageMap, SitemapNode
from aeo.reference.business_input import BusinessInput
from aeo.reference.framework import load_framework
from aeo.report.packager import AssetBundle, build_asset_bundle, build_checklist


def _blueprint() -> Blueprint:
    return Blueprint(
        topic="CTEM",
        version=2,
        sitemap=[
            SitemapNode(slug="/resources", title="Resources", page_type="pillar",
                        intent="informational", journey_stage="awareness", cluster="core",
                        seed_questions=["What is CTEM?"], required_entities=["CTEM"], priority=0.9),
            SitemapNode(slug="/what-is-ctem", title="What is CTEM", page_type="blog",
                        intent="informational", journey_stage="awareness", cluster="core", priority=0.7),
            SitemapNode(slug="/contact", title="Contact", page_type="contact",
                        intent="commercial", journey_stage="decision", priority=0.4),
        ],
        coverage=CoverageMap(
            required_entities=["CTEM", "CVSS"],
            clusters=[CoverageCluster(name="core", pillar_slug="/resources",
                                      supporting_slugs=["/what-is-ctem"], min_pages=3)],
        ),
    ).with_hash()


def test_bundle_has_all_core_assets() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=3)
    kinds = {a.kind for a in bundle.assets}
    assert {"readme", "sitemap", "nav", "content_briefs", "linking", "schema", "page_spec"} <= kinds
    paths = {a.path for a in bundle.assets}
    assert "sitemap.xml" in paths
    assert any(p.startswith("pages/") for p in paths)


def test_sitemap_is_valid_xml_with_absolute_urls() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="https://acme.com", draft_limit=0)
    sitemap = next(a for a in bundle.assets if a.kind == "sitemap")
    root = ET.fromstring(sitemap.content)  # parses → valid XML
    locs = [el.text for el in root.iter() if el.tag.endswith("loc")]
    assert "https://acme.com/resources" in locs
    assert "https://acme.com/contact" in locs


def test_page_specs_are_bounded_and_carry_jsonld() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=2)
    specs = [a for a in bundle.assets if a.kind == "page_spec"]
    assert len(specs) == 2  # draft_limit honored
    assert all("JSON-LD" in s.content for s in specs)
    assert all("```json" in s.content for s in specs)


def test_strategy_asset_only_when_profile_given() -> None:
    bp = _blueprint()
    assert not any(a.kind == "strategy" for a in build_asset_bundle(blueprint=bp, draft_limit=0).assets)
    profile = {"domain": "acme.com", "scenario": "small_site", "deliverable": "Gap Analysis & Build Plan",
               "narrative": "…", "business_intent": {"model": "saas", "decided_by": "deterministic"},
               "classification": {"site_class": "small", "page_count": 3},
               "journey": {"gaps": ["conversion"]}, "actions": [{"priority": 1, "category": "content",
               "title": "x", "effort": "low"}]}
    assert any(a.kind == "strategy" for a in build_asset_bundle(blueprint=bp, profile=profile, draft_limit=0).assets)


def test_write_materializes_files(tmp_path) -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=2)
    written = bundle.write(tmp_path)
    assert len(written) == len(bundle.assets) + 1  # every asset + manifest.json
    assert (tmp_path / "sitemap.xml").exists()
    assert (tmp_path / "content-briefs.md").exists()
    assert (tmp_path / "manifest.json").exists()
    assert list((tmp_path / "pages").glob("*.md"))
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["asset_count"] == len(bundle.assets)


def test_to_zip_bytes_is_a_valid_zip_with_every_asset() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=2)
    data = bundle.to_zip_bytes()
    assert data[:2] == b"PK"  # zip magic
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert "sitemap.xml" in names
        assert "manifest.json" in names
        assert any(n.startswith("pages/") for n in names)
        # every declared asset is present in the archive
        for a in bundle.assets:
            assert a.path in names
        assert zf.read("sitemap.xml").decode("utf-8").startswith("<?xml")


def test_bundle_from_brief_plan_no_website(tmp_path) -> None:
    # End-to-end with SP-2: a no-website brief → blueprint → bundle.
    framework = load_framework()
    plan = plan_from_brief(BusinessInput(name="Acme", domain="acme.com"), framework=framework)
    bundle = build_asset_bundle(blueprint=plan.blueprint, coverage=plan.coverage,
                                profile=plan.profile.to_dict(), origin="acme.com", draft_limit=3)
    assert isinstance(bundle, AssetBundle)
    assert any(a.kind == "strategy" for a in bundle.assets)  # profile present
    # every blueprint page appears in the sitemap
    sitemap = next(a for a in bundle.assets if a.kind == "sitemap")
    assert sitemap.content.count("<loc>") == len(plan.blueprint.sitemap)
    bundle.write(tmp_path)
    assert (tmp_path / "README.md").exists()


# ── builder_mode (owner-mode kits) ───────────────────────────────────────────

_BUSINESS = {"name": "Harbor Dental", "category": "Healthcare & Medical",
             "location": "Boston, US", "services": ["implants", "whitening"]}


def test_dev_mode_is_unchanged_by_builder_mode_param() -> None:
    bp = _blueprint()
    before = build_asset_bundle(blueprint=bp, origin="acme.com", draft_limit=2)
    after = build_asset_bundle(blueprint=bp, origin="acme.com", draft_limit=2, builder_mode="dev",
                               business=_BUSINESS)
    assert [(a.path, a.kind, a.content) for a in before.assets] == \
           [(a.path, a.kind, a.content) for a in after.assets]


def test_diy_mode_assets() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=2,
                                builder_mode="diy", business=_BUSINESS)
    paths = {a.path for a in bundle.assets}
    assert {"START-HERE.md", "get-found-now.md", "platform-tips.md"} <= paths
    assert "README.md" not in paths  # replaced by START-HERE at the root
    assert "for-your-developer/README.md" in paths
    assert "for-your-developer/sitemap.xml" in paths
    drafts = [a for a in bundle.assets if a.kind == "page_draft"]
    assert len(drafts) == 2  # draft_limit honored
    assert all("Create a page called" in d.content for d in drafts)
    assert all("Optional technical extra" in d.content for d in drafts)
    start = next(a for a in bundle.assets if a.path == "START-HERE.md")
    assert "Harbor Dental" in start.content
    assert "Week 1" in start.content


def test_ai_mode_prompts_without_llm_calls() -> None:
    class _ExplodingLLM:  # any draft call would touch .enabled — prove none happens
        @property
        def enabled(self):
            raise AssertionError("ai mode must not call the LLM")

    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=3,
                                builder_mode="ai", business=_BUSINESS, llm=_ExplodingLLM())
    prompts = [a for a in bundle.assets if a.kind == "prompt"]
    assert len(prompts) == 3
    assert not any(a.kind in ("page_draft", "page_spec") for a in bundle.assets)
    assert any(a.path == "prompts/how-to-use.md" for a in bundle.assets)
    resources = next(a for a in prompts if "resources" in a.path)
    assert "Harbor Dental" in resources.content
    assert "What is CTEM?" in resources.content  # seed question embedded


def test_hire_mode_adds_job_post_and_checklist() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=1,
                                builder_mode="hire", business=_BUSINESS)
    paths = {a.path for a in bundle.assets}
    assert "hire-someone/job-post.md" in paths
    assert "hire-someone/acceptance-checklist.md" in paths
    assert any(a.kind == "page_draft" for a in bundle.assets)  # diy drafts included
    job = next(a for a in bundle.assets if a.path == "hire-someone/job-post.md")
    assert "Harbor Dental" in job.content
    checklist = next(a for a in bundle.assets if a.path == "hire-someone/acceptance-checklist.md")
    assert "validator.schema.org" in checklist.content


def test_get_found_now_uses_category_directories() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=0,
                                builder_mode="diy", business=_BUSINESS)
    found = next(a for a in bundle.assets if a.path == "get-found-now.md")
    assert "Google Business Profile" in found.content
    assert "Zocdoc" in found.content  # healthcare directory matched from the category
    assert "Boston, US" in found.content


def test_checklist_weeks_mirror_start_here() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=0,
                                builder_mode="diy", business=_BUSINESS)
    nodes = [n for n in _blueprint().sitemap]
    checklist = build_checklist(sorted(nodes, key=lambda n: (-n.priority, n.slug)), "diy")
    assert checklist["total"] == 3 + 4  # 3 pages (one week) + 4 visibility tasks
    week1 = checklist["weeks"][0]
    assert week1["tasks"][0]["id"] == "page:/resources"  # priority order, stable ids
    final = checklist["weeks"][-1]
    assert {t["id"] for t in final["tasks"]} == {"vis:gbp", "vis:listings", "vis:reviews", "vis:readthrough"}
    # every page task appears in START-HERE too (same source data)
    start = next(a for a in bundle.assets if a.path == "START-HERE.md")
    assert "Resources" in start.content


def test_checklist_dev_mode_has_no_visibility_week() -> None:
    nodes = sorted(_blueprint().sitemap, key=lambda n: (-n.priority, n.slug))
    checklist = build_checklist(nodes, "dev")
    assert all(not t["id"].startswith("vis:") for w in checklist["weeks"] for t in w["tasks"])


def test_owner_mode_zip_contains_nested_paths() -> None:
    bundle = build_asset_bundle(blueprint=_blueprint(), origin="acme.com", draft_limit=1,
                                builder_mode="hire", business=_BUSINESS)
    with zipfile.ZipFile(io.BytesIO(bundle.to_zip_bytes())) as zf:
        names = set(zf.namelist())
        assert "hire-someone/job-post.md" in names
        assert "for-your-developer/sitemap.xml" in names
