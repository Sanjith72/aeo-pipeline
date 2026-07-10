"""agent_runs repo — offline API + pure-helper checks (no Postgres)."""

from __future__ import annotations

import string


def test_exposes_its_api() -> None:
    from aeo.storage.repos import agent_runs

    for fn in ("new_id", "create", "get", "by_idempotency_key", "append_step", "steps_for",
               "set_status", "list_by_status", "count_active"):
        assert callable(getattr(agent_runs, fn))


def test_new_id_is_unique_and_urlsafe() -> None:
    from aeo.storage.repos import agent_runs

    ids = {agent_runs.new_id() for _ in range(200)}
    assert len(ids) == 200
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert all(set(i) <= allowed and len(i) >= 8 for i in ids)
