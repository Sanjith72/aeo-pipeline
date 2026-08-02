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
    confirm_slugs,
    evaluate,
    gather_site_signals,
    page_candidates,
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
    assert specs[0].title == "Quick Wins"
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


def test_plan_to_milestones_carries_current_state_and_prompts():
    # 0024: current_state + prompts ride through to the milestone specs so the unified
    # TaskHowTo expander can render the "Where you are now" line and the "Doing it with AI"
    # box on milestone tasks. Page tasks carry a prompts dict; visibility tasks carry none —
    # and an absent prompts must stay None (never coerced to a dict), which is what the
    # DIY-tab de-dup guard keys on to decide whether to show the AI box.
    plan = {
        "phases": [
            {
                "key": "week_1",
                "title": "Week 1",
                "tasks": [
                    {
                        "id": "page:/services/x",
                        "label": "Create the “X” page",
                        "action_required": "Create it.",
                        "how_to": "Draft it.",
                        "current_state": "This page doesn't exist on your site yet.",
                        "prompts": {"ai": "Write the X page…", "human": "Write it yourself…"},
                    },
                    {
                        "id": "vis:gbp",
                        "label": "Claim your Google Business Profile",
                        "action_required": "Claim it.",
                        "how_to": "Step 1.",
                        "current_state": "Unverified in Google's local data.",
                    },
                ],
            }
        ]
    }
    page_task, vis_task = plan_to_milestones(plan)[0].tasks
    assert page_task.current_state == "This page doesn't exist on your site yet."
    assert page_task.prompts == {"ai": "Write the X page…", "human": "Write it yourself…"}
    assert vis_task.current_state == "Unverified in Google's local data."
    assert vis_task.prompts is None


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


def test_evaluate_matches_renamed_slug_by_last_segment():
    # Sites move /services/x to /x (or /our-services/x) freely — still the same page.
    signals = SiteSignals(slugs={"/teeth-whitening"})
    assert "page:/services/teeth-whitening" in evaluate(_tasks(), signals)


def test_evaluate_ignores_mere_mentions_in_copy():
    """A page task is about a PAGE existing. Talking about the topic is not the same thing.

    This is the regression that mattered most: the old heading/offering/nav fallback
    fuzzy-matched a slug's last segment against live text, so an ordinary site verified
    most of its roadmap on the first check without publishing anything."""
    signals = SiteSignals(
        slugs={"/", "/about-us", "/services", "/contact", "/blog"},
        headings=["Emergency Plumbing Services in Austin", "Why Choose Us"],
        services=["Emergency Plumbing", "Drain Cleaning"],
        nav_labels=["Home", "About Us", "Services", "Contact", "Blog", "FAQs", "EN"],
    )
    tasks = [
        {"task_key": k, "verify_kind": "page", "verify_target": k}
        for k in (
            "/about",                        # nav "About Us"
            "/faq",                          # nav "FAQs"
            "/services/emergency-plumbing",  # an <h1> mentions it
            "/services/drain-cleaning",      # a listed service mentions it
            "/locations/austin",             # "...in Austin" inside a heading
            "/contact-us",                   # nav "Contact"
        )
    ]
    assert evaluate(tasks, signals) == set()


def test_evaluate_tail_match_requires_a_multi_segment_target():
    """`/blog/pricing` must not satisfy a `/pricing` task — the tail rule exists to absorb
    a renamed PREFIX, not to match any page that happens to end the same way."""
    tasks = [{"task_key": "p", "verify_kind": "page", "verify_target": "/pricing"}]
    assert evaluate(tasks, SiteSignals(slugs={"/blog/pricing"})) == set()
    assert evaluate(tasks, SiteSignals(slugs={"/pricing"})) == {"p"}


def test_label_match_is_whole_token_not_substring():
    """A two-letter language switcher must not verify an unrelated service page: the old
    bidirectional substring test accepted "en" inside "emergency"."""
    tasks = [{"task_key": "svc", "verify_kind": "service", "verify_target": "Emergency Plumbing"}]
    assert evaluate(tasks, SiteSignals(services=["EN"], nav_labels=["EN"])) == set()
    assert evaluate(tasks, SiteSignals(services=["Emergency Plumbing Services"])) == {"svc"}


def test_label_match_needs_more_than_one_generic_word():
    tasks = [{"task_key": "svc", "verify_kind": "service", "verify_target": "Services"}]
    assert evaluate(tasks, SiteSignals(services=["Services"], nav_labels=["Services"])) == set()


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
    # ...but it must NOT read as "we looked and found nothing". Callers branch on this to
    # avoid telling the user their published work isn't live when we never reached them.
    assert sig.reachable is False
    assert sig.pages_fetched == 0


def test_gather_site_signals_reports_reachable_when_it_reads_the_site():
    async def fake_fetch(url: str):
        return _HTML if url.rstrip("/").endswith("harbor.com") else None

    sig = asyncio.run(gather_site_signals("harbor.com", fetch=fake_fetch))
    assert sig.reachable is True
    assert sig.blocked is False
    assert sig.pages_fetched == 1


def test_gather_site_signals_flags_a_bot_wall_as_blocked():
    """A 200 challenge stub is not the site. Parsing it yields empty signals, which used to
    be indistinguishable from an honest "nothing published yet"."""

    async def walled(url: str):
        return "<html><head><title>Just a moment...</title></head><body>Client Challenge</body></html>"

    sig = asyncio.run(gather_site_signals("walled.example", fetch=walled))
    assert sig.reachable is False
    assert sig.blocked is True


# ── confirm_slugs (a referenced slug is not a live page) ─────────────────────


def test_confirm_slugs_keeps_only_urls_that_resolve():
    """Sitemap <loc>s and nav hrefs are never fetched upstream, so a stale entry or a broken
    link would otherwise mark a 404 as "verified live"."""

    async def fetch(url: str):
        return "<html>ok</html>" if url.endswith("/real") else None

    confirmed = asyncio.run(
        confirm_slugs({"/real", "/stale"}, domain="harbor.com", fetch=fetch)
    )
    assert confirmed == {"/real"}


def test_page_candidates_exposes_slugs_needing_confirmation():
    signals = SiteSignals(slugs={"/services/teeth-whitening"})
    assert page_candidates(_tasks(), signals) == {
        "page:/services/teeth-whitening": "/services/teeth-whitening"
    }


# ── verify_client_milestones (the "Check my site now" flow, offline) ─────────
#
# Proves the exact question the button exists to answer: publish one of the plan's
# suggested pages, click the button, and does that task flip (which is what moves the
# dashboard's progress bar)? DB + network are both injected, so this runs offline.

_BARE_HOME = "<html><body><h1>Harbor Dental</h1></body></html>"


def _fetch_serving(live: set[str]):
    """A site whose homepage links to nothing — so a page can only be found by probing
    its exact recommended URL, the path that survives sitemap truncation."""

    async def fetch(url: str):
        path = path_slug(url)
        if path == "/":
            return _BARE_HOME
        return "<html><body><h1>a real page</h1></body></html>" if path in live else None

    return fetch


def _stub_repo(monkeypatch, *, pending: list[dict], baselined: bool) -> dict:
    from aeo.pipeline import milestone_audit as ma

    recorded: dict = {}

    def _mark(client_id, keys, run_id, *, source="crawl"):
        recorded["keys"] = sorted(keys)
        recorded["source"] = source
        return len(keys)

    monkeypatch.setattr(ma.milestones_repo, "pending_verifiable", lambda cid: pending)
    monkeypatch.setattr(ma.milestones_repo, "is_baselined", lambda cid: baselined)
    monkeypatch.setattr(ma.milestones_repo, "mark_baselined", lambda cid: recorded.setdefault("baselined", True))
    monkeypatch.setattr(ma.milestones_repo, "mark_verified", _mark)
    return recorded


_PAGE_TASK = [
    {"task_key": "page:/services/teeth-whitening", "verify_kind": "page",
     "verify_target": "/services/teeth-whitening"},
]


def test_publishing_a_suggested_page_verifies_it(monkeypatch):
    """The headline behaviour: the suggested page is now live, so the task flips and the
    progress roll-up that feeds the bar gains a verified task."""
    from aeo.pipeline.milestone_audit import verify_client_milestones

    rec = _stub_repo(monkeypatch, pending=_PAGE_TASK, baselined=True)
    out = asyncio.run(
        verify_client_milestones(
            1, "harbor.com", fetch=_fetch_serving({"/services/teeth-whitening"})
        )
    )
    assert out["newly_verified"] == 1
    assert out["verified_keys"] == ["page:/services/teeth-whitening"]
    assert rec["source"] == "crawl"  # credited as real, published work


def test_unpublished_page_stays_pending(monkeypatch):
    from aeo.pipeline.milestone_audit import verify_client_milestones

    _stub_repo(monkeypatch, pending=_PAGE_TASK, baselined=True)
    out = asyncio.run(verify_client_milestones(1, "harbor.com", fetch=_fetch_serving(set())))
    assert out["newly_verified"] == 0
    assert out["site_reachable"] is True  # we DID read the site — an honest "not yet"


def test_first_run_baselines_instead_of_claiming_credit(monkeypatch):
    """A page that was already there before the plan existed is marked done, but reported
    as already_live — never as a change the owner published."""
    from aeo.pipeline.milestone_audit import verify_client_milestones

    rec = _stub_repo(monkeypatch, pending=_PAGE_TASK, baselined=False)
    out = asyncio.run(
        verify_client_milestones(
            1, "harbor.com", fetch=_fetch_serving({"/services/teeth-whitening"})
        )
    )
    assert out["baselined"] is True
    assert out["already_live"] == 1
    assert out["newly_verified"] == 0
    assert rec["source"] == "baseline"
    assert rec["baselined"] is True


def test_unreachable_site_is_not_reported_as_nothing_published(monkeypatch):
    from aeo.pipeline.milestone_audit import verify_client_milestones

    async def dead(url: str):
        return None

    _stub_repo(monkeypatch, pending=_PAGE_TASK, baselined=True)
    out = asyncio.run(verify_client_milestones(1, "harbor.com", fetch=dead))
    assert out["site_reachable"] is False
    assert out["newly_verified"] == 0


def test_confirm_slugs_caps_work_without_optimistically_accepting():
    """Over the cap we leave slugs UNconfirmed rather than assuming they're live —
    under-claiming is the safe direction for a signal that permanently marks work done."""

    async def fetch(url: str):
        return "<html>ok</html>"

    slugs = {f"/p{i}" for i in range(10)}
    confirmed = asyncio.run(confirm_slugs(slugs, domain="x.com", fetch=fetch, limit=3))
    assert len(confirmed) == 3
    assert confirmed < slugs
