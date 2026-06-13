"""Instrumentation (Block F) — offline guards (no DB).

Mirrors the Retention Engine's offline style (see test_outcomes.py): the migration
is asserted by reading its SQL and the repo is asserted importable (the pool is
lazy). The metric SQL needs a live Postgres, so the record + DAU/return-rate/
quick-win/implementation-rate round-trips are pinned in
tests/integration/test_db_smoke.py.
"""

from __future__ import annotations

from aeo.storage import migrate
from aeo.storage.repos import events as events_repo


class TestMigration0011:
    def _sql(self) -> str:
        paths = {v: p for v, _n, p in migrate._discover()}
        assert "0011" in paths, "migration 0011 not discovered"
        return paths["0011"].read_text(encoding="utf-8")

    def test_creates_events_table(self):
        assert "CREATE TABLE IF NOT EXISTS events" in self._sql()

    def test_has_required_columns(self):
        sql = self._sql()
        for col in ("session_id", "client_id", "event_type", "url", "metadata", "created_at"):
            assert col in sql, f"events.{col} missing from migration"
        assert "JSONB" in sql  # metadata is JSONB

    def test_has_dau_and_session_indexes(self):
        sql = self._sql()
        assert "idx_events_type_created" in sql  # (event_type, created_at) for DAU/rollups
        assert "idx_events_session" in sql       # session_id for return-rate

    def test_client_fk_is_dangling_safe(self):
        # a pruned client must not delete its events — set null, don't cascade
        sql = self._sql().upper()
        assert "REFERENCES CLIENTS(ID) ON DELETE SET NULL" in sql

    def test_is_additive(self):
        sql = self._sql().upper()
        assert "DROP TABLE" not in sql
        assert "DROP COLUMN" not in sql
        assert "ALTER TABLE" not in sql


class TestReposImportable:
    def test_events_repo_exposes_api(self):
        assert callable(events_repo.record)
        assert callable(events_repo.dau)
        assert callable(events_repo.return_rate)
        assert callable(events_repo.quick_win_completion_rate)
        assert callable(events_repo.recommendation_implementation_rate)
        assert callable(events_repo.metrics)

    def test_event_type_vocab_matches_frontend(self):
        # the metric SQL keys on these literals — keep them in lockstep with web/lib/api.ts
        assert events_repo.PLAN_VIEWED == "plan_viewed"
        assert events_repo.TASK_MARKED_DONE == "task_marked_done"
        assert events_repo.SESSION_START == "session_start"
        assert events_repo.RETURN_VISIT == "return_visit"
