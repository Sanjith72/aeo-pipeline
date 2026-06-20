"""
Developer Handoff brief generation — pure, offline.

`generate_dev_brief` turns one milestone task into developer-ready instructions shaped by
its verify_kind; `attach_dev_briefs` walks a dashboard payload. No DB, no network.
"""

from __future__ import annotations

import json
import re

from aeo.report.dev_brief import (
    attach_dev_briefs,
    generate_dev_brief,
    generate_diy_steps,
    generate_raw_snippet,
)


def _page_task(**over):
    base = {
        "verify_kind": "page",
        "verify_target": "/services/commercial-roofing",
        "label": "Create the “Commercial Roofing” page",
        "how_to": "Use the draft.",
    }
    base.update(over)
    return base


def test_page_brief_has_absolute_url_when_origin_known():
    brief = generate_dev_brief(_page_task(), origin="acme-roofing.com")
    assert "https://acme-roofing.com/services/commercial-roofing" in brief
    assert "TYPE: New page" in brief


def test_page_brief_falls_back_to_path_without_origin():
    brief = generate_dev_brief(_page_task())
    assert "URL PATH: /services/commercial-roofing" in brief
    assert "https://" not in brief.split("HOW WE VERIFY")[0].replace("https://schema.org", "")


def test_page_brief_embeds_valid_jsonld_with_title_from_label():
    brief = generate_dev_brief(_page_task(), origin="acme.com")
    # Pull the JSON out of the <script> block and confirm it parses + carries the title.
    m = re.search(r"<script[^>]*>\s*(\{.*?\})\s*</script>", brief, re.S)
    assert m, "expected a JSON-LD <script> block"
    data = json.loads(m.group(1))
    assert data["@context"] == "https://schema.org"
    assert data["name"] == "Commercial Roofing"  # extracted from the curly-quoted label
    # /services/ slug → Service schema, with provider + serviceType.
    assert data["@type"] == "Service"
    assert data["serviceType"] == "Commercial Roofing"


def test_page_schema_type_varies_by_slug():
    def kind_for(slug):
        b = generate_dev_brief(_page_task(verify_target=slug), origin="x.com")
        return json.loads(re.search(r"(\{.*?\})", b, re.S).group(1))["@type"]

    assert kind_for("/about") == "AboutPage"
    assert kind_for("/contact") == "ContactPage"
    assert kind_for("/blog/why-roofs-leak") == "Article"
    assert kind_for("/team") == "WebPage"  # no special match


def test_heading_brief_specifies_exact_tag():
    brief = generate_dev_brief(
        {"verify_kind": "heading", "verify_target": "Commercial Roofing", "label": "Add a heading"}
    )
    assert "<h2>Commercial Roofing</h2>" in brief
    assert "DOM" in brief


def test_service_brief_lists_offering_and_jsonld():
    brief = generate_dev_brief(
        {"verify_kind": "service", "verify_target": "Drone Roof Inspection", "label": "Add an offering"}
    )
    assert "Drone Roof Inspection" in brief
    assert '"@type": "Service"' in brief
    # suggests a slugified service path
    assert "/services/drone-roof-inspection" in brief


def test_manual_brief_is_offsite_and_uses_how_to():
    brief = generate_dev_brief(
        {"verify_kind": "manual", "verify_target": None, "label": "Claim your Google Business Profile",
         "how_to": "Follow step 1 in get-found-now.md."}
    )
    assert "Off-site" in brief
    assert "Follow step 1" in brief
    assert "mark it done in the dashboard" in brief.lower()


def test_generate_handles_missing_target_gracefully():
    # A page kind with no target degrades to the manual brief rather than emitting junk.
    brief = generate_dev_brief({"verify_kind": "page", "verify_target": "", "label": "Do a thing"})
    assert "Do a thing" in brief


def test_attach_dev_briefs_walks_every_task():
    dash = {
        "milestones": [
            {"tasks": [_page_task(), {"verify_kind": "manual", "verify_target": None, "label": "GBP"}]},
            {"tasks": [{"verify_kind": "heading", "verify_target": "Our Team", "label": "Add team heading"}]},
        ],
        "progress": {"total": 3},
    }
    out = attach_dev_briefs(dash, origin="acme.com")
    assert out is dash  # mutates in place + returns
    briefs = [t["dev_brief"] for m in dash["milestones"] for t in m["tasks"]]
    assert len(briefs) == 3 and all(b.startswith("TASK:") for b in briefs)


# ── raw_snippet ──────────────────────────────────────────────────────────────


def test_raw_snippet_for_page_is_only_the_jsonld_script():
    snippet = generate_raw_snippet(_page_task(), origin="acme.com")
    assert snippet is not None
    assert snippet.startswith('<script type="application/ld+json">')
    assert snippet.rstrip().endswith("</script>")
    data = json.loads(re.search(r"(\{.*\})", snippet, re.S).group(1))
    assert data["@type"] == "Service"
    assert data["url"] == "https://acme.com/services/commercial-roofing"
    # strictly the snippet — no "TASK:" prose like the dev brief carries
    assert "TASK:" not in snippet


def test_raw_snippet_for_heading_is_only_the_tag():
    snippet = generate_raw_snippet(
        {"verify_kind": "heading", "verify_target": "Our Services", "label": "Add a heading"}
    )
    assert snippet == "<h2>Our Services</h2>"


def test_raw_snippet_is_none_for_manual_and_targetless():
    assert generate_raw_snippet({"verify_kind": "manual", "verify_target": None, "label": "GBP"}) is None
    assert generate_raw_snippet({"verify_kind": "heading", "verify_target": "", "label": "x"}) is None


# ── diy_steps ────────────────────────────────────────────────────────────────


def test_diy_steps_schema_are_cms_specific():
    wp = generate_diy_steps(_page_task(), cms_type="wordpress")
    shop = generate_diy_steps(_page_task(), cms_type="shopify")
    unknown = generate_diy_steps(_page_task(), cms_type=None)
    assert any("WordPress Admin" in s for s in wp)
    assert any("header.php" in s for s in wp)
    assert any("Shopify Admin" in s for s in shop)
    assert any("theme.liquid" in s for s in shop)
    assert any("content management system" in s.lower() for s in unknown)
    # all schema flows tell the user to paste before </head>
    for steps in (wp, shop, unknown):
        assert any("</head>" in s for s in steps)


def test_diy_steps_unknown_cms_for_unrecognized_value():
    # An unrecognised cms_type falls back to the CMS-agnostic steps, not an error.
    steps = generate_diy_steps(_page_task(), cms_type="wix")
    assert any("content management system" in s.lower() for s in steps)


def test_diy_steps_heading_edits_on_page_not_head():
    steps = generate_diy_steps(
        {"verify_kind": "heading", "verify_target": "Our Team", "label": "Add heading"},
        cms_type="wordpress",
    )
    assert any("H2" in s for s in steps)
    assert not any("</head>" in s for s in steps)


def test_diy_steps_manual_is_offsite_attestation():
    steps = generate_diy_steps(
        {"verify_kind": "manual", "verify_target": None, "label": "GBP"}, cms_type="shopify"
    )
    assert any("outside your website" in s for s in steps)
    assert any("dashboard" in s.lower() for s in steps)


def test_attach_dev_briefs_attaches_snippet_and_steps():
    dash = {
        "milestones": [{"tasks": [_page_task(), {"verify_kind": "manual", "verify_target": None, "label": "GBP"}]}],
    }
    attach_dev_briefs(dash, origin="acme.com", cms_type="wordpress")
    page, manual = dash["milestones"][0]["tasks"]
    assert page["raw_snippet"].startswith("<script")
    assert any("WordPress" in s for s in page["diy_steps"])
    assert manual["raw_snippet"] is None
    assert manual["diy_steps"]  # off-site steps still present
