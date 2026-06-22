"""Migration 0019 (agent runtime) — offline schema assertions, no Postgres."""

from __future__ import annotations

from aeo.storage import migrate


def _sql() -> str:
    paths = {v: path for v, _n, path in migrate._discover()}
    assert "0019" in paths, "migration 0019 not discovered"
    return paths["0019"].read_text(encoding="utf-8")


def test_discovered_with_expected_name() -> None:
    names = {v: n for v, n, _p in migrate._discover()}
    assert names.get("0019") == "agent_runs"


def test_creates_both_tables() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS agent_runs" in sql
    assert "CREATE TABLE IF NOT EXISTS agent_steps" in sql


def test_agent_runs_has_idempotency_and_status_guard() -> None:
    sql = _sql()
    assert "idempotency_key  TEXT UNIQUE" in sql or "idempotency_key TEXT UNIQUE" in sql
    for status in ("queued", "planning", "staged", "approved", "rejected", "failed", "cancelled"):
        assert status in sql


def test_steps_cascade_and_unique_seq() -> None:
    sql = _sql()
    assert "REFERENCES agent_runs(id) ON DELETE CASCADE" in sql
    assert "UNIQUE (run_id, seq)" in sql


def test_reuses_updated_at_trigger() -> None:
    assert "FOR EACH ROW EXECUTE FUNCTION set_updated_at()" in _sql()
