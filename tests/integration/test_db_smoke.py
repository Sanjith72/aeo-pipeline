"""
Integration: DB schema + repo round-trip against a real Postgres.

Gated — skips unless a database is reachable (``health_check()``), so the offline
suite stays green with no DB while CI / local runs with ``docker compose up -d db``
get real coverage of the migrations and the SQL the repos emit. It applies
migrations (idempotent), then exercises the page_priorities round-trip that the
new Site-Discovery → Prioritization path depends on.
"""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check


@pytest.fixture(scope="module")
def db():
    if not health_check():
        pytest.skip("no reachable database — set DATABASE_URL and start Postgres to run")
    from aeo.storage.migrate import apply_pending

    apply_pending()  # idempotent; brings a fresh DB up to the latest schema
    return True


def test_migrations_apply_idempotently(db):
    from aeo.storage.migrate import apply_pending

    # A second application must be a no-op (every migration is guarded).
    assert apply_pending() == []


def test_page_priorities_round_trip(db):
    from aeo.storage.repos import priorities as priorities_repo
    from aeo.storage.repos import runs as runs_repo

    run = runs_repo.start(label="db-smoke")
    try:
        priorities_repo.upsert(
            run.id, "https://securin.io/", "homepage",
            base_weight=0.7, traffic_signal=20.0, final_score=14.0,
            final_rank=1, selected=True,
        )
        priorities_repo.upsert(
            run.id, "https://securin.io/login", "utility",
            base_weight=0.2, traffic_signal=1.0, final_score=0.2,
            final_rank=2, selected=False,
        )

        # selected_urls returns only the selected rows, ranked.
        assert priorities_repo.selected_urls(run.id) == ["https://securin.io/"]
        # the full ranking keeps both rows for observability.
        assert {r["url"] for r in priorities_repo.ranking(run.id)} == {
            "https://securin.io/",
            "https://securin.io/login",
        }
    finally:
        runs_repo.finish(run.id, status="succeeded")


def test_blueprint_versioning_round_trip(db):
    """v4: save_versioned reuses on identical inputs and bumps on change, and
    pin_run records the version on the run."""
    import uuid

    from aeo.reference.generator import generate_blueprint
    from aeo.storage.repos import blueprints as blueprints_repo
    from aeo.storage.repos import runs as runs_repo

    # unique topic per run — leftover rows from earlier runs must not flip `reused`
    topic = f"PEV-smoke-{uuid.uuid4().hex[:8]}"
    bp = generate_blueprint(topic=topic, llm=None)
    first = blueprints_repo.save_versioned(bp)
    again = blueprints_repo.save_versioned(bp)  # identical inputs → reuse
    assert again.reused is True
    assert again.blueprint.version == first.blueprint.version

    # A structural change bumps the version.
    from aeo.reference.blueprint import SitemapNode

    changed = bp.model_copy(update={"sitemap": [*bp.sitemap, SitemapNode(slug="/smoke-extra", title="X", page_type="pillar")]})
    bumped = blueprints_repo.save_versioned(changed)
    assert bumped.reused is False
    assert bumped.blueprint.version == first.blueprint.version + 1

    run = runs_repo.start(label="db-smoke-bp")
    try:
        blueprints_repo.pin_run(run.id, bumped.id)
        assert blueprints_repo.latest(topic).blueprint.version == bumped.blueprint.version
    finally:
        runs_repo.finish(run.id, status="succeeded")


def test_coverage_and_feedback_round_trip(db):
    """v4: coverage_diffs upsert + criteria_refinements proposal round-trip."""
    from aeo.processor.coverage_diff import DiscoveredPage, coverage_diff
    from aeo.reference.feedback import CriteriaRefinement
    from aeo.reference.generator import generate_blueprint
    from aeo.storage.repos import coverage as coverage_repo
    from aeo.storage.repos import feedback as feedback_repo
    from aeo.storage.repos import runs as runs_repo

    run = runs_repo.start(label="db-smoke-cov")
    try:
        bp = generate_blueprint(topic="PEV-smoke", llm=None)
        result = coverage_diff(bp, [DiscoveredPage.from_url("https://securin.io/what-is-ctem", "pillar")])
        coverage_repo.put(
            run.id, blueprint_id=None, target_id=None, coverage_pct=result.coverage_pct,
            missing_count=len(result.missing), thin_count=len(result.thin_clusters),
            detail=result.to_detail(),
        )
        row = coverage_repo.get(run.id)
        assert row is not None and row["missing_count"] == len(result.missing)

        rid = feedback_repo.save_refinement(
            CriteriaRefinement(criterion="stats_in_html", current_target=4, proposed_target=5,
                               rationale="smoke", evidence={"cited_n": 4})
        )
        proposed = feedback_repo.list_refinements("proposed")
        assert any(r["id"] == rid for r in proposed)
        feedback_repo.set_refinement_status(rid, "accepted")
        assert all(r["id"] != rid for r in feedback_repo.list_refinements("proposed"))
    finally:
        runs_repo.finish(run.id, status="succeeded")


def _smoke_page(url: str, content_hash: str, run_id: int):
    from aeo.storage.models import FetchedPage
    from aeo.storage.repos import pages as pages_repo
    from aeo.storage.repos import targets as targets_repo

    # crawled_pages.chk_single_owner demands exactly one owner — register a reusable
    # smoke client (idempotent upsert) instead of writing an ownerless row.
    client = targets_repo.upsert("DB Smoke Client", "db-smoke-client.example")
    return pages_repo.upsert(
        FetchedPage(
            url=url, url_normalized=url, success=True, http_status=200,
            fetch_duration_ms=1, html="<html></html>", markdown="", title="t",
            meta_description="", error=None, content_hash=content_hash,
        ),
        run_id=run_id, client_id=client.id, competitor_id=None,
    )


def test_recent_observations_correlates_score_to_citation_run(db):
    """v4 fix: a citation's rubric tiers must come from the citation's OWN run, not
    whichever run scored the page most recently."""
    from aeo.storage.models import CriterionScore, PageScore
    from aeo.storage.repos import feedback as feedback_repo
    from aeo.storage.repos import runs as runs_repo
    from aeo.storage.repos import scores as scores_repo

    run_a = runs_repo.start(label="db-smoke-fb-a")
    run_b = runs_repo.start(label="db-smoke-fb-b")
    try:
        page = _smoke_page("https://securin.io/feedback-corr", "hashfbcorr", run_a.id)

        # The classic 8 rubric columns are NOT NULL — a real score always carries them
        # (only the two v3 additions are nullable), so the fixture must too.
        _CRITERIA = ["schema_markup", "qa_blocks", "stats_in_html", "entity_consistency",
                     "heading_structure", "content_depth", "citation_signals", "load_speed"]

        def _score(run_id: int, tier: int) -> PageScore:
            return PageScore(
                page_id=page.id, run_id=run_id,
                criteria={name: CriterionScore(name=name, value=tier) for name in _CRITERIA},
                total=tier, max_possible=50, priority_tier="medium",
            )

        # Run B's score (tier 5) is inserted FIRST, run A's (tier 2) SECOND, so run A
        # is the globally-latest by scored_at. The citation is in run B → correct tier 5.
        scores_repo.put(_score(run_b.id, 5), scored_by="test")
        scores_repo.put(_score(run_a.id, 2), scored_by="test")
        feedback_repo.record_citation(
            page_id=page.id, run_id=run_b.id,
            url="https://securin.io/feedback-corr", question="what is x?", cited=True,
        )

        obs = [o for o in feedback_repo.recent_observations() if o.page_id == page.id]
        assert len(obs) == 1
        assert obs[0].cited is True
        assert obs[0].tiers.get("stats_in_html") == 5  # run B's tier, not run A's
    finally:
        runs_repo.finish(run_a.id, status="succeeded")
        runs_repo.finish(run_b.id, status="succeeded")


def test_target_upsert_reuses_row_when_same_domain_under_new_name(db):
    """Regression: re-auditing the same domain under a different label raised
    ``clients_domain_key`` (the upsert was idempotent on name only). The domain owns
    identity — the second upsert must reuse the row and refresh the label."""
    from aeo.storage.db import transaction
    from aeo.storage.repos import targets as targets_repo

    host = "upsert-smoke.example"
    try:
        first = targets_repo.upsert("Upsert Smoke A", host)
        second = targets_repo.upsert("Upsert Smoke B", f"https://www.{host}/")  # same host, new label
        assert second.id == first.id
        assert second.domain == host
        assert second.name == "Upsert Smoke B"  # label refreshed

        # rename-collision guard: a third row owns the label → reuse keeps the old name
        other = targets_repo.upsert("Upsert Smoke C", "upsert-smoke-other.example")
        kept = targets_repo.upsert("Upsert Smoke C", host)  # name taken by `other`
        assert kept.id == first.id
        assert kept.name == "Upsert Smoke B"  # unchanged, no unique-name violation
        assert other.id != first.id
    finally:
        with transaction() as conn, conn.cursor() as cur:  # leave no smoke rows behind
            cur.execute("DELETE FROM clients WHERE domain IN (%s, %s)", (host, "upsert-smoke-other.example"))


def test_recent_observations_keeps_unscored_cited_page(db):
    """v4 fix: LEFT JOIN — a cited page with no matching-run score still surfaces
    (empty tiers), instead of being silently dropped by an INNER JOIN."""
    from aeo.storage.repos import feedback as feedback_repo
    from aeo.storage.repos import runs as runs_repo

    run = runs_repo.start(label="db-smoke-fb-unscored")
    try:
        page = _smoke_page("https://securin.io/feedback-unscored", "hashfbun", run.id)
        feedback_repo.record_citation(
            page_id=page.id, run_id=run.id,
            url="https://securin.io/feedback-unscored", question="q?", cited=True,
        )
        obs = [o for o in feedback_repo.recent_observations() if o.page_id == page.id]
        assert len(obs) == 1       # present despite no score
        assert obs[0].tiers == {}  # empty tiers → downstream skips per-criterion
    finally:
        runs_repo.finish(run.id, status="succeeded")


def test_recommendation_outcome_lifecycle(db):
    """Retention Engine (#11): open a pending outcome at issue time → a re-crawl with
    the SAME content hash stays pending → a re-crawl with a CHANGED hash flips it to
    'implemented' with the detecting run recorded. Detection keys on url_normalized
    (stable), not the per-run page id."""
    from aeo.storage.db import transaction
    from aeo.storage.repos import outcomes as outcomes_repo
    from aeo.storage.repos import recommendations as recs_repo
    from aeo.storage.repos import runs as runs_repo

    url = "https://securin.io/retention-loop"
    issue = runs_repo.start(label="db-smoke-outcome-issue")
    recrawl = runs_repo.start(label="db-smoke-outcome-recrawl")
    try:
        baseline_hash = "hashbaseline" + "0" * 52
        page = _smoke_page(url, baseline_hash, issue.id)
        rec_id = recs_repo.create(
            page.id, issue.id, "schema", {"title": "Add FAQPage JSON-LD"},
            criterion="schema_markup",
        )
        oid = outcomes_repo.open(
            rec_id, page.id, page.url_normalized,
            baseline_run_id=issue.id, baseline_hash=baseline_hash, criterion="schema_markup",
        )

        # Re-crawl, content unchanged → no flip, outcome still pending.
        assert outcomes_repo.mark_from_recrawl(page.url_normalized, recrawl.id, baseline_hash) == 0
        assert any(o["id"] == oid for o in outcomes_repo.pending_for_url(page.url_normalized))

        # Re-crawl, content changed → flips to implemented with the detecting run.
        changed_hash = "hashchanged" + "1" * 53
        assert outcomes_repo.mark_from_recrawl(page.url_normalized, recrawl.id, changed_hash) == 1
        done = outcomes_repo.for_page(page.id, status="implemented")
        assert len(done) == 1
        assert done[0]["id"] == oid
        assert done[0]["detected_run_id"] == recrawl.id
        assert done[0]["detection_method"] == "content_hash_changed"
        assert done[0]["detected_at"] is not None

        # No pending outcomes remain for the URL — a second re-crawl is a no-op.
        assert outcomes_repo.pending_for_url(page.url_normalized) == []
    finally:
        with transaction() as conn, conn.cursor() as cur:  # leave no outcome rows behind
            cur.execute("DELETE FROM recommendation_outcomes WHERE url_normalized = %s", (url,))
        runs_repo.finish(issue.id, status="succeeded")
        runs_repo.finish(recrawl.id, status="succeeded")
