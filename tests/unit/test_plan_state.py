"""Offline schema/repo smoke test for the retention foundation (B1) — no Postgres.

Mirrors test_storage_schema: the repo only touches the DB at call time (the pool is
lazy), so importing it and inspecting migration 0012 needs no connection.
"""

from __future__ import annotations

import string

from aeo.storage import migrate


class TestPlanStatesMigration:
    def _sql(self) -> str:
        paths = {v: path for v, _n, path in migrate._discover()}
        assert "0012" in paths, "migration 0012 not discovered"
        return paths["0012"].read_text(encoding="utf-8")

    def test_discovered_with_expected_name(self):
        names = {v: n for v, n, _p in migrate._discover()}
        assert names.get("0012") == "plan_states"

    def test_creates_plan_states_table(self):
        assert "CREATE TABLE IF NOT EXISTS plan_states" in self._sql()

    def test_session_resume_index_present(self):
        assert "idx_plan_states_session" in self._sql()

    def test_keeps_plan_payload_and_progress_columns(self):
        sql = self._sql()
        for col in ("plan", "profile", "done_task_ids", "score_snapshot", "session_id", "run_id"):
            assert col in sql

    def test_run_id_fk_survives_run_deletion(self):
        # A pruned crawl_run must not cascade-delete a user's saved plan.
        assert "REFERENCES crawl_runs(id) ON DELETE SET NULL" in self._sql()


class TestPlanStateRepo:
    def test_exposes_its_api(self):
        from aeo.storage.repos import plan_state

        assert callable(plan_state.create)
        assert callable(plan_state.get)
        assert callable(plan_state.update_progress)
        assert callable(plan_state.latest_for_session)

    def test_new_id_is_unique_and_urlsafe(self):
        from aeo.storage.repos import plan_state

        ids = {plan_state.new_id() for _ in range(200)}
        assert len(ids) == 200  # no collisions
        allowed = set(string.ascii_letters + string.digits + "-_")
        assert all(set(i) <= allowed and len(i) >= 8 for i in ids)
