"""Retention Engine (#11, #12) — offline guards (no DB).

Mirrors the project's offline test style: the migration is asserted by reading
its SQL, the repo is asserted importable (the pool is lazy), and the completion
decision is exercised through the pure ``decide_status`` helper so the
issue → unchanged → changed lifecycle is testable without a connection. The
live-DB round-trip lives in tests/integration/test_db_smoke.py.
"""

from __future__ import annotations

from aeo.recommender import Recommendation
from aeo.recommender.models import CONTENT, SCHEMA
from aeo.report.builder import _rec
from aeo.storage import migrate
from aeo.storage.repos import outcomes as outcomes_repo
from aeo.storage.repos.outcomes import IMPLEMENTED, decide_status


class TestMigration0010:
    def _sql(self) -> str:
        paths = {v: p for v, _n, p in migrate._discover()}
        assert "0010" in paths, "migration 0010 not discovered"
        return paths["0010"].read_text(encoding="utf-8")

    def test_creates_recommendation_outcomes_table(self):
        assert "CREATE TABLE IF NOT EXISTS recommendation_outcomes" in self._sql()

    def test_pins_baseline_and_status_domain(self):
        sql = self._sql()
        assert "baseline_hash" in sql and "baseline_run_id" in sql
        assert "detected_run_id" in sql and "detected_at" in sql
        for status in ("pending", "implemented", "regressed", "not_detected"):
            assert f"'{status}'" in sql

    def test_has_page_status_index(self):
        # The playbook calls for an index on (page_id, status).
        assert "idx_rec_outcomes_page_status" in self._sql()

    def test_keys_detection_on_url_not_just_page(self):
        # crawled_pages is per-run, so detection must key on the stable url.
        sql = self._sql()
        assert "url_normalized" in sql
        assert "idx_rec_outcomes_url_status" in sql

    def test_is_additive(self):
        # Must not alter existing tables destructively.
        sql = self._sql().upper()
        assert "DROP TABLE" not in sql
        assert "DROP COLUMN" not in sql


class TestReposImportable:
    def test_outcomes_repo_exposes_api(self):
        assert callable(outcomes_repo.open)
        assert callable(outcomes_repo.mark_from_recrawl)
        assert callable(outcomes_repo.decide_status)
        assert callable(outcomes_repo.for_page)
        assert callable(outcomes_repo.pending_for_url)


class TestDecideStatus:
    """The pure completion decision — the lifecycle, DB-free."""

    def test_unchanged_hash_stays_pending(self):
        # identical hash → no change → leave the outcome pending
        assert decide_status("a" * 64, "a" * 64) is None

    def test_changed_hash_flips_to_implemented(self):
        assert decide_status("a" * 64, "b" * 64) == IMPLEMENTED

    def test_indeterminate_when_a_hash_is_missing(self):
        # Can't conclude anything without both hashes → keep watching.
        assert decide_status(None, "b" * 64) is None
        assert decide_status("a" * 64, None) is None
        assert decide_status(None, None) is None


class TestThreeFieldContract:
    """#12 — every recommendation surfaces current_state / action_required / how_to."""

    def test_payload_carries_three_fields_via_derivation(self):
        rec = Recommendation(
            rec_type=SCHEMA, criterion="schema_markup",
            title="Add FAQPage JSON-LD", rationale="The body has Q&A but no FAQPage schema.",
        )
        p = rec.to_payload()
        # All three present and non-empty even when the generator set none of them.
        assert p["current_state"] == "The body has Q&A but no FAQPage schema."  # falls back to rationale
        assert p["action_required"] == "Add FAQPage JSON-LD"                    # falls back to title
        assert "JSON-LD" in p["how_to"]                                          # schema how-to fallback

    def test_explicit_fields_win_over_derivation(self):
        rec = Recommendation(
            rec_type=CONTENT, criterion="content_depth", title="Expand the section",
            rationale="thin", current_state="Section is 80 words.",
            action_required="Grow it to ~400 words.", how_to="Add two subsections with stats.",
        )
        p = rec.to_payload()
        assert p["current_state"] == "Section is 80 words."
        assert p["action_required"] == "Grow it to ~400 words."
        assert p["how_to"] == "Add two subsections with stats."

    def test_report_rec_renders_three_fields(self):
        rec = Recommendation(
            rec_type=CONTENT, criterion="content_depth",
            title="Expand the overview", rationale="The overview is too thin.",
        )
        out = _rec(rec)
        assert {"current_state", "action_required", "how_to"} <= set(out)
        assert out["action_required"] == "Expand the overview"
        assert out["current_state"] == "The overview is too thin."
