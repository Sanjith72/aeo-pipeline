"""Offline schema/repo smoke test for the v3 build — no Postgres required.

Covers migrations 0004-0008 and the 5 new repo modules. The repo functions only
touch the DB at call time (via ``transaction()``), and ``db.get_pool()`` is lazy,
so importing the modules and inspecting the migration files needs no connection.
"""

from __future__ import annotations

import pytest

from aeo.storage import migrate

# version -> (migration name, primary table it must create)
EXPECTED_MIGRATIONS = {
    "0004": ("prioritization", "page_priorities"),
    "0005": ("gap_and_recs", "gap_analyses"),
    "0006": ("reports", "page_reports"),
    "0007": ("observability", "agent_traces"),
}


class TestMigrationsDiscoverable:
    def test_all_four_present_with_numeric_prefix(self):
        discovered = {v: name for v, name, _path in migrate._discover()}
        for version, (name, _table) in EXPECTED_MIGRATIONS.items():
            assert version in discovered, f"migration {version} not discovered"
            assert discovered[version] == name

    @pytest.mark.parametrize("version", sorted(EXPECTED_MIGRATIONS))
    def test_migration_creates_expected_table(self, version):
        paths = {v: path for v, _n, path in migrate._discover()}
        sql = paths[version].read_text(encoding="utf-8")
        table = EXPECTED_MIGRATIONS[version][1]
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    def test_recommendations_table_also_created(self):
        paths = {v: path for v, _n, path in migrate._discover()}
        sql = paths["0005"].read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS recommendations" in sql


class TestRubricV3Migration:
    """0008 expands rubric_scores_v2 from 8 to 10 criteria (max 40 → 50)."""

    def _sql(self) -> str:
        paths = {v: path for v, _n, path in migrate._discover()}
        assert "0008" in paths, "migration 0008 not discovered"
        return paths["0008"].read_text(encoding="utf-8")

    def test_adds_two_new_score_columns_idempotently(self):
        sql = self._sql()
        for col in ("render_accessibility_score", "answer_readability_score"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in sql

    def test_new_columns_are_nullable_with_range_check(self):
        # Nullable so pre-v3 rows stay valid; CHECK still bounds populated tiers 1-5.
        sql = self._sql()
        assert "render_accessibility_score IS NULL OR render_accessibility_score BETWEEN 1 AND 5" in sql
        assert "answer_readability_score IS NULL OR answer_readability_score BETWEEN 1 AND 5" in sql

    def test_bumps_max_possible_default_to_50(self):
        assert "ALTER COLUMN max_possible_score SET DEFAULT 50" in self._sql()

    def test_view_refreshed_with_new_columns(self):
        sql = self._sql()
        assert "DROP VIEW IF EXISTS page_score_view" in sql
        assert "s.render_accessibility_score" in sql
        assert "s.answer_readability_score" in sql


class TestReposImportable:
    """Importing a repo must not require a DB (pool is lazy)."""

    def test_all_new_repos_expose_their_api(self):
        from aeo.storage.repos import (
            gaps,
            priorities,
            recommendations,
            reports,
            traces,
        )

        assert callable(priorities.upsert)
        assert callable(priorities.selected_urls)
        assert callable(gaps.put)
        assert callable(gaps.get)
        assert callable(recommendations.create)
        assert callable(recommendations.set_validation)
        assert callable(reports.put)
        assert callable(reports.set_review_status)
        assert callable(traces.record)
        assert callable(traces.for_page)
