"""Gamification migrations 0020-0022 — offline schema assertions."""

from __future__ import annotations

from aeo.storage import migrate


def _sql(version: str) -> str:
    paths = {v: p for v, _n, p in migrate._discover()}
    assert version in paths, f"migration {version} not discovered"
    return paths[version].read_text(encoding="utf-8")


def test_state_table_keyed_on_session_with_maturity_guard() -> None:
    sql = _sql("0020")
    assert "CREATE TABLE IF NOT EXISTS gamification_state" in sql
    assert "session_id        TEXT        PRIMARY KEY" in sql or "session_id TEXT PRIMARY KEY" in sql
    for stage in ("foundations", "on_radar", "recommended", "authority", "cited_leader"):
        assert stage in sql
    assert "FOR EACH ROW EXECUTE FUNCTION set_updated_at()" in sql


def test_awards_ledger_is_idempotent_on_source() -> None:
    sql = _sql("0021")
    assert "CREATE TABLE IF NOT EXISTS gamification_awards" in sql
    assert "UNIQUE (award_type, source_table, source_id)" in sql


def test_achievements_seeded() -> None:
    sql = _sql("0022")
    assert "CREATE TABLE IF NOT EXISTS achievement_definitions" in sql
    assert "CREATE TABLE IF NOT EXISTS achievement_unlocks" in sql
    assert "UNIQUE (session_id, code)" in sql
    assert "'recommended'" in sql and "'top_answer'" in sql
