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


def test_events_record_and_retention_metrics(db):
    """Instrumentation (Block F): record events across days, then assert each metric.
    The `events` table is new and only this test writes to it, so whole-table
    aggregates (DAU / return-rate / quick-win) are isolated once our rows are cleaned
    up; the implementation-rate is checked against a direct count so it holds whatever
    else is in recommendation_outcomes."""
    import json
    from datetime import UTC, datetime, timedelta

    from aeo.storage.db import transaction
    from aeo.storage.repos import events as events_repo
    from aeo.storage.repos import outcomes as outcomes_repo
    from aeo.storage.repos import recommendations as recs_repo
    from aeo.storage.repos import runs as runs_repo

    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    d0, d1 = now, now - timedelta(days=1)
    url_a = "https://securin.io/evt-impl-a"
    url_b = "https://securin.io/evt-impl-b"

    def _ins(cur, session_id, event_type, created_at, metadata=None):
        cur.execute(
            "INSERT INTO events (session_id, event_type, metadata, created_at) "
            "VALUES (%s, %s, %s::jsonb, %s)",
            (session_id, event_type, json.dumps(metadata or {}), created_at),
        )

    issue = runs_repo.start(label="db-smoke-evt-issue")
    recrawl = runs_repo.start(label="db-smoke-evt-recrawl")
    try:
        with transaction() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE session_id LIKE 'smoke-evt-%'")
            # 'ret' is active two distinct days → a returning session; 'one' a single day.
            _ins(cur, "smoke-evt-ret", events_repo.SESSION_START, d1)
            _ins(cur, "smoke-evt-ret", events_repo.WIZARD_STEP_COMPLETED, d0, {"step": 1})
            _ins(cur, "smoke-evt-one", events_repo.SESSION_START, d0)
            # quick-win funnel: 3 presented, q1 done as a quick win, q2 done but NOT a
            # quick win (excluded), q3 never done → completion rate = 1/3.
            _ins(cur, "smoke-evt-ret", events_repo.PLAN_VIEWED, d0,
                 {"quick_win_ids": ["q1", "q2", "q3"]})
            _ins(cur, "smoke-evt-ret", events_repo.TASK_MARKED_DONE, d0,
                 {"task_id": "q1", "quick_win": True})
            _ins(cur, "smoke-evt-ret", events_repo.TASK_MARKED_DONE, d0,
                 {"task_id": "q2", "quick_win": False})

        # record() happy path (today, third distinct session-day combination is 'one'+today
        # already; this adds an event to 'one' so it stays a single-day session).
        assert isinstance(events_repo.record("smoke-evt-one", events_repo.PLAN_VIEWED), int)

        # DAU over our window: day d1 → {ret}; day d0 → {ret, one}.
        series = {row["day"]: row["dau"] for row in events_repo.dau(d1, d0 + timedelta(days=1))}
        assert series.get(d1.date()) == 1
        assert series.get(d0.date()) == 2

        # return-rate: of {ret, one}, only 'ret' spans 2+ days → 0.5.
        assert abs(events_repo.return_rate() - 0.5) < 1e-9
        # quick-win completion: of {q1,q2,q3} presented, only q1 was done as a quick win.
        assert abs(events_repo.quick_win_completion_rate() - (1 / 3)) < 1e-9

        # implementation-rate: one implemented + one pending outcome. Compare the repo's
        # number against a direct count so leftover rows can't make this flaky.
        page_a = _smoke_page(url_a, "hashimpl_a" + "0" * 54, issue.id)
        page_b = _smoke_page(url_b, "hashimpl_b" + "0" * 54, issue.id)
        rec_a = recs_repo.create(page_a.id, issue.id, "schema", {"title": "A"}, criterion="schema_markup")
        rec_b = recs_repo.create(page_b.id, issue.id, "schema", {"title": "B"}, criterion="schema_markup")
        outcomes_repo.open(rec_a, page_a.id, page_a.url_normalized,
                           baseline_run_id=issue.id, baseline_hash="hashimpl_a" + "0" * 54)
        outcomes_repo.open(rec_b, page_b.id, page_b.url_normalized,
                           baseline_run_id=issue.id, baseline_hash="hashimpl_b" + "0" * 54)
        # flip A to implemented via a changed hash; B stays pending.
        assert outcomes_repo.mark_from_recrawl(page_a.url_normalized, recrawl.id, "changed" + "1" * 57) == 1

        with transaction() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FILTER (WHERE status='implemented')::float "
                "/ NULLIF(COUNT(*), 0) AS rate FROM recommendation_outcomes"
            )
            expected_impl = float(cur.fetchone()["rate"])
        assert expected_impl > 0  # our implemented row guarantees a positive rate
        assert abs(events_repo.recommendation_implementation_rate() - expected_impl) < 1e-9
    finally:
        with transaction() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE session_id LIKE 'smoke-evt-%'")
            cur.execute("DELETE FROM recommendation_outcomes WHERE url_normalized IN (%s, %s)",
                        (url_a, url_b))
        runs_repo.finish(issue.id, status="succeeded")
        runs_repo.finish(recrawl.id, status="succeeded")
