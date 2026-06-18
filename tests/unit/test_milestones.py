"""
Implementation Milestones — the pure halves, offline.

`plan_to_milestones` (plan → milestone/task specs + derived verification signals) and
`milestone_verify.evaluate` / `signals_from_docs` (does the live site satisfy a pending
task?) carry the feature's logic and are fully testable with no DB and no network.
"""

from __future__ import annotations

import asyncio

from aeo.intelligence.milestone_verify import (
    SiteSignals,
    evaluate,
    gather_site_signals,
    path_slug,
    signals_from_docs,
)
from aeo.intelligence.site_facts import FetchedDoc
from aeo.report.milestones import plan_to_milestones

# A representative build_plan() payload: two phases, page tasks + a visibility win.
_PLAN = {
    "phases": [
        {
            "key": "week_1",
            "title": "Week 1 — essentials",
            "blurb": "do these first",
            "tasks": [
                {
                    "id": "page:/services/teeth-whitening",
                    "label": "Create the “Teeth Whitening” page",
                    "action_required": "Create the page at /services/teeth-whitening.",
                    "how_to": "Use the draft.",
                },
                {
                    "id": "vis:gbp",
                    "label": "Claim your Google Business Profile",
                    "action_required": "Claim and fill it out.",
                    "how_to": "Step 1.",
                },
            ],
        },
        {
            "key": "later",
            "title": "Later",
            "blurb": "nice to have",
            "tasks": [
                {"id": "page:/about", "label": "Create the “About” page",
                 "action_required": "Create /about.", "how_to": "Write it."},
            ],
        },
    ],
}


# ── plan → milestones ──────────────────────────────────────────────────────


def test_plan_to_milestones_maps_phases_and_verify_signals():
    specs = plan_to_milestones(_PLAN)
    assert [s.milestone_key for s in specs] == ["week_1", "later"]
    assert specs[0].title == "Phase 1 — Core setup"
    assert specs[0].position == 0 and specs[1].position == 1

    page_task, vis_task = specs[0].tasks
    # Page tasks derive a 'page' signal keyed on their slug; visibility wins are manual.
    assert (page_task.verify_kind, page_task.verify_target) == ("page", "/services/teeth-whitening")
    assert (vis_task.verify_kind, vis_task.verify_target) == ("manual", None)
    assert page_task.position == 0 and vis_task.position == 1


def test_plan_to_milestones_unknown_phase_falls_back_to_its_title():
    specs = plan_to_milestones({"phases": [{"key": "q3", "title": "Quarter 3", "tasks": []}]})
    assert specs[0].title == "Quarter 3" and specs[0].milestone_key == "q3"


def test_plan_to_milestones_empty_plan():
    assert plan_to_milestones({}) == []


# ── path normalization ──────────────────────────────────────────────────────


def test_path_slug_normalizes_url_and_slug_forms():
    assert path_slug("https://x.com/Services/Teeth-Whitening/") == "/services/teeth-whitening"
    assert path_slug("/services/teeth-whitening") == "/services/teeth-whitening"
    assert path_slug("https://x.com/") == "/"


# ── evaluate (pure verification decision) ────────────────────────────────────


def _tasks():
    return [
        {"task_key": "page:/services/teeth-whitening", "verify_kind": "page",
         "verify_target": "/services/teeth-whitening"},
        {"task_key": "page:/about", "verify_kind": "page", "verify_target": "/about"},
        {"task_key": "vis:gbp", "verify_kind": "manual", "verify_target": None},
    ]


def test_evaluate_verifies_page_when_slug_is_live():
    signals = SiteSignals(slugs={"/services/teeth-whitening", "/contact"})
    assert evaluate(_tasks(), signals) == {"page:/services/teeth-whitening"}


def test_evaluate_matches_renamed_slug_by_heading():
    # Site shipped the page under a different slug but the offering shows in a heading.
    signals = SiteSignals(slugs={"/whitening"}, headings=["Teeth Whitening"])
    done = evaluate(_tasks(), signals)
    assert "page:/services/teeth-whitening" in done


def test_evaluate_never_auto_verifies_manual_tasks():
    signals = SiteSignals(slugs={"/services/teeth-whitening", "/about"}, nav_labels=["gbp"])
    done = evaluate(_tasks(), signals)
    assert "vis:gbp" not in done
    assert done == {"page:/services/teeth-whitening", "page:/about"}


def test_evaluate_empty_signals_verifies_nothing():
    assert evaluate(_tasks(), SiteSignals()) == set()


def test_evaluate_service_and_heading_kinds():
    tasks = [
        {"task_key": "svc", "verify_kind": "service", "verify_target": "Dental Implants"},
        {"task_key": "hd", "verify_kind": "heading", "verify_target": "Our Team"},
    ]
    signals = SiteSignals(services=["Dental Implants"], headings=["Meet Our Team"])
    assert evaluate(tasks, signals) == {"svc", "hd"}


# ── signals_from_docs (pure scrape fold) ─────────────────────────────────────

_HTML = """
<html><body>
<nav><a href="/services/teeth-whitening">Teeth Whitening</a>
     <a href="/about">About</a></nav>
<h1>Harbor Dental</h1><h2>Teeth Whitening</h2>
</body></html>
"""


def test_signals_from_docs_collects_slugs_headings_nav():
    docs = [FetchedDoc(url="https://harbor.com/", html=_HTML)]
    sig = signals_from_docs(docs, domain="harbor.com")
    assert "/services/teeth-whitening" in sig.slugs
    assert "/about" in sig.slugs
    assert "/" in sig.slugs  # the homepage itself
    assert "Teeth Whitening" in sig.headings
    assert "Teeth Whitening" in sig.nav_labels


# ── gather_site_signals (injectable fetch, best-effort) ──────────────────────


def test_gather_site_signals_uses_injected_fetch_and_merges_discovered():
    async def fake_fetch(url: str):
        return _HTML if url.rstrip("/").endswith("harbor.com") else None

    sig = asyncio.run(
        gather_site_signals(
            "harbor.com", fetch=fake_fetch, discovered_slugs=["https://harbor.com/deep/page"]
        )
    )
    assert "/services/teeth-whitening" in sig.slugs
    assert "/deep/page" in sig.slugs  # discovered inventory folded in


def test_gather_site_signals_dead_homepage_is_empty_not_fatal():
    async def dead_fetch(url: str):
        return None

    sig = asyncio.run(gather_site_signals("nope.example", fetch=dead_fetch))
    assert sig.slugs == set() and sig.services == []
