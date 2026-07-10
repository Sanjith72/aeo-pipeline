"""Approved-run export: stored draft payloads → launch-kit assets, no re-generation."""

from __future__ import annotations

from aeo.agents.export import bundle_from_run


def _run(tasks) -> dict:
    return {"id": "runX", "domain": "acme.com", "result": {"domain": "acme.com", "tasks": tasks}}


def _task(slug: str, *, flagged: bool = False, with_draft: bool = True) -> dict:
    t = {
        "id": f"page:{slug}",
        "title": f"Create: {slug.strip('/').title() or 'Home'}",
        "slug": slug,
        "page_type": "service",
        "node": {"slug": slug, "page_type": "service", "intent": "commercial"},
    }
    if with_draft:
        t["draft"] = {
            "h1": "What we do",
            "body_markdown": f"# {slug}\n\nApproved copy for {slug}.",
            "jsonld": [{"@type": "WebPage", "name": slug}],
            "generator": "llm",
            "draft_quality": "full",
        }
    if flagged:
        t["critic"] = {"needs_review": True, "claims": ["#1 provider"]}
    return t


def test_bundle_has_readme_plus_one_page_per_draft() -> None:
    bundle = bundle_from_run(_run([_task("/about"), _task("/services/roofing"), _task("/faq", with_draft=False)]))
    paths = [a.path for a in bundle.assets]
    assert paths == ["README.md", "pages/about.md", "pages/services-roofing.md"]  # no draft → no asset
    assert bundle.name == "agent-run-runX"


def test_page_asset_renders_the_stored_payload_verbatim() -> None:
    bundle = bundle_from_run(_run([_task("/about")]))
    page = bundle.assets[1]
    assert page.kind == "page_spec"
    assert "Approved copy for /about." in page.content       # the approved body, untouched
    assert "generator=llm · quality=full" in page.content    # provenance header
    assert '"@type": "WebPage"' in page.content              # JSON-LD fenced block
    assert "## JSON-LD (paste into <head>)" in page.content


def test_readme_counts_pages_and_surfaces_critic_flags() -> None:
    bundle = bundle_from_run(_run([_task("/about"), _task("/pricing", flagged=True)]))
    readme = bundle.assets[0].content
    assert "2 page draft(s)" in readme
    assert "claims to verify" in readme  # the reviewer accepted a flagged draft — say so
    assert "pages/pricing.md" in readme


def test_empty_or_missing_result_yields_just_a_readme() -> None:
    bundle = bundle_from_run({"id": "runY", "result": None})
    assert [a.path for a in bundle.assets] == ["README.md"]
    assert "0 page draft(s)" in bundle.assets[0].content
