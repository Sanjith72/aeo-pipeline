# Agent Runtime Walking Skeleton — Implementation Plan (Phase 2A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a durable, resumable agent-run loop on the existing Postgres job queue — enqueue → deterministic Planner stages a task graph → it lands in a human approval gate → approve/reject — proving the assistive-copilot spine end-to-end with zero new infrastructure.

**Architecture:** A deterministic `AgentRunController` (the agent-era sibling of `audit_cycle`) drives typed transitions persisted to two new tables (`agent_runs`, `agent_steps`). It reuses `jobs_repo` (`FOR UPDATE SKIP LOCKED`) for scheduling via a new `AGENT_RUN` job kind on the existing `Worker`. The Planner is deterministic-only (wraps `resolve_framework` + `plan_from_brief`, LLM off) so this slice is fully testable offline. Every run ends `staged` and only a human `approve`/`reject` advances it — the assistive-copilot + human-gate constraint, in code.

**Tech Stack:** Python 3.11, psycopg2 + RealDictCursor, FastAPI, Typer, pytest. Follows existing repo conventions: module-function repos over `transaction()`, numbered idempotent SQL migrations, offline unit tests (assert migration SQL + API existence + pure logic via DI) plus a live-DB integration test.

---

## Scope

**In scope (this plan):** the agent runtime, durable state schema, deterministic Planner, worker wiring, the approve/reject API, and the CLI entry point. This is a complete, testable vertical slice: you can enqueue an agent run, a worker plans it deterministically, it stages a task graph, and a human approves/rejects it via the API.

**Out of scope — follow-on plans (do NOT build here):**
- **Plan 2B — Research + Builder agents + hybrid LLM routing.** Per-call frontier/local routing + cost trace on `LLMClient`; the Builder that drafts pages/FAQ/schema (staged); the Research agent. Introduces the first real LLM spend, so it ships with cost accounting and dedupe tokens.
- **Plan 2C — Critic pipeline + frontend + SSE.** Deterministic checks + model-isolated adversarial/claim auditor; `web/components/AgentReviewQueue.tsx`; per-step SSE streaming.

This skeleton deliberately keeps the Planner deterministic (no LLM) so it is fast and offline-testable; the LLM seams are introduced in 2B.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/aeo/settings.py` | Modify | Add `AgentsCfg` (concurrency, step timeout, retry budget) + `agents` field on `Settings`. |
| `src/aeo/storage/migrations/0019_agent_runs.sql` | Create | `agent_runs` (run state) + `agent_steps` (per-step trace/resume log). |
| `src/aeo/storage/repos/agent_runs.py` | Create | Module-function repo: `new_id`, `create` (idempotent), `get`, `append_step`, `steps_for`, `set_status`, `list_by_status`. |
| `src/aeo/agents/__init__.py` | Create | Package marker + public re-exports. |
| `src/aeo/agents/planner.py` | Create | `plan_tasks(brief) -> dict`: deterministic task graph from blueprint sitemap (no DB, no LLM required). |
| `src/aeo/agents/runtime.py` | Create | `AgentRunController` (plan→staged loop, DI-testable) + `brief_from_dict` + `start_agent_run`. |
| `src/aeo/pipeline/worker.py` | Modify | `AGENT_RUN` kind, `enqueue_agent_run`, dispatch to the controller; add `AGENT_RUN` to default kinds. |
| `src/aeo/cli.py` | Modify | `aeo agent` command — enqueue a run from a brief. |
| `src/aeo/api/app.py` | Modify | `POST /api/agent/run`, `GET /api/agent/run/{id}`, `POST .../approve`, `POST .../reject`. |
| `tests/unit/test_agents_settings.py` | Create | `AgentsCfg` defaults + env override. |
| `tests/unit/test_agent_runs_schema.py` | Create | Migration 0019 discovered, tables/columns/FK/trigger present. |
| `tests/unit/test_agent_runs_repo.py` | Create | Repo API existence + `new_id` uniqueness (offline). |
| `tests/unit/test_agent_planner.py` | Create | Deterministic task graph from a no-website brief (offline, LLM off). |
| `tests/unit/test_agent_runtime.py` | Create | Controller transitions + failure path via injected fake repo/planner. |
| `tests/unit/test_agent_worker.py` | Create | `AGENT_RUN` dispatch routes to the controller (offline). |
| `tests/unit/test_agent_api.py` | Create | Endpoint wiring via `TestClient` + monkeypatched repo/service. |
| `tests/integration/test_agent_runs_db.py` | Create | Live-DB create→step→status round-trip. |

**Conventions to follow exactly:** repos are module functions using `with transaction() as conn, conn.cursor() as cur:` and `json.dumps(payload, default=str)` for JSONB; cursors are `RealDictCursor` so `cur.fetchone()["col"]` works. Migrations are `CREATE ... IF NOT EXISTS` and reuse `set_updated_at()` (from 0001). Run all tests with `python -m pytest` from the repo root (`pythonpath=src` is configured in `pyproject.toml`).

---

### Task 1: AgentsCfg settings

**Files:**
- Modify: `src/aeo/settings.py` (add a section class near the other `*Cfg` classes, and a field on `Settings`)
- Test: `tests/unit/test_agents_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agents_settings.py
"""AgentsCfg: defaults + AEO__AGENTS__* env override."""

from __future__ import annotations


def test_agents_cfg_defaults() -> None:
    from aeo.settings import AgentsCfg

    cfg = AgentsCfg()
    assert cfg.concurrency == 2
    assert cfg.step_timeout_sec == 120
    assert cfg.max_attempts == 3


def test_settings_exposes_agents_section() -> None:
    from aeo.settings import AgentsCfg, Settings

    s = Settings()
    assert isinstance(s.agents, AgentsCfg)


def test_agents_env_override(monkeypatch) -> None:
    from aeo.settings import Settings

    monkeypatch.setenv("AEO__AGENTS__CONCURRENCY", "5")
    s = Settings()
    assert s.agents.concurrency == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agents_settings.py -v`
Expected: FAIL with `ImportError: cannot import name 'AgentsCfg'`.

- [ ] **Step 3: Add the section class**

Add immediately after `class ScoringCfg(BaseModel):` (…) block in `src/aeo/settings.py`:

```python
class AgentsCfg(BaseModel):
    # Phase 2 agent runtime: scoped LLM agents driven by a deterministic controller on the
    # existing Postgres job queue (no new broker). concurrency caps how many AGENT_RUN jobs a
    # worker drains at once; step_timeout_sec bounds a single agent step (Phase 2B enforces it
    # per-LLM-call); max_attempts is the per-run retry budget before the run is marked failed.
    concurrency: int = 2
    step_timeout_sec: int = 120
    max_attempts: int = 3
```

- [ ] **Step 4: Register the field on `Settings`**

In `class Settings(BaseSettings):`, add the field next to `scoring: ScoringCfg = ScoringCfg()`:

```python
    agents: AgentsCfg = AgentsCfg()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agents_settings.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/aeo/settings.py tests/unit/test_agents_settings.py
git commit -m "feat(agents): add AgentsCfg settings section"
```

---

### Task 2: Migration 0019 — agent_runs + agent_steps

**Files:**
- Create: `src/aeo/storage/migrations/0019_agent_runs.sql`
- Test: `tests/unit/test_agent_runs_schema.py`

- [ ] **Step 1: Write the failing test** (offline — asserts on the migration SQL text, mirroring `test_plan_state.py`)

```python
# tests/unit/test_agent_runs_schema.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_runs_schema.py -v`
Expected: FAIL on `assert "0019" in paths`.

- [ ] **Step 3: Write the migration**

```sql
-- src/aeo/storage/migrations/0019_agent_runs.sql
-- Phase 2A: durable, resumable agent runs on the existing Postgres job queue (no new broker).
-- agent_runs is the run-state spine; agent_steps is the per-step trace + resume log. Every run
-- ends 'staged' and only a human approve/reject advances it (assistive-copilot + human gate).
-- Additive and idempotent. set_updated_at() is from 0001; clients(id) is from 0001.

CREATE TABLE IF NOT EXISTS agent_runs (
    id               TEXT         PRIMARY KEY,              -- minted token (repos/agent_runs.new_id)
    idempotency_key  TEXT UNIQUE,                            -- collapse duplicate enqueues (NULL = no dedupe)
    domain           TEXT,
    client_id        INTEGER      REFERENCES clients(id) ON DELETE SET NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','planning','staged','approved','rejected','failed','cancelled')),
    current_step     VARCHAR(40),
    brief            JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- the BusinessInput that seeded the run
    result           JSONB,                                      -- the staged task graph (Planner output)
    error            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_domain ON agent_runs (domain);

DROP TRIGGER IF EXISTS trg_agent_runs_updated_at ON agent_runs;
CREATE TRIGGER trg_agent_runs_updated_at
    BEFORE UPDATE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS agent_steps (
    id          BIGSERIAL    PRIMARY KEY,
    run_id      TEXT         NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq         INTEGER      NOT NULL,
    agent       VARCHAR(40)  NOT NULL,                       -- 'planner' | 'research' | 'builder' | 'critic'
    tool        VARCHAR(80),                                 -- the wrapped engine seam, e.g. 'plan_from_brief'
    status      VARCHAR(20)  NOT NULL DEFAULT 'ok'
        CHECK (status IN ('ok','failed','skipped')),
    model       VARCHAR(80),                                 -- LLM model used (NULL for deterministic steps)
    tokens      INTEGER,
    cost_usd    NUMERIC(10,6),                               -- per-step frontier cost (Phase 2B fills this)
    latency_ms  INTEGER,
    error_class VARCHAR(40),                                 -- exception type for failure triage
    detail      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps (run_id, seq);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_runs_schema.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/storage/migrations/0019_agent_runs.sql tests/unit/test_agent_runs_schema.py
git commit -m "feat(agents): migration 0019 agent_runs + agent_steps"
```

---

### Task 3: agent_runs repo

**Files:**
- Create: `src/aeo/storage/repos/agent_runs.py`
- Test: `tests/unit/test_agent_runs_repo.py`

- [ ] **Step 1: Write the failing test** (offline — API existence + pure `new_id`, mirroring `test_plan_state.py::TestPlanStateRepo`)

```python
# tests/unit/test_agent_runs_repo.py
"""agent_runs repo — offline API + pure-helper checks (no Postgres)."""

from __future__ import annotations

import string


def test_exposes_its_api() -> None:
    from aeo.storage.repos import agent_runs

    for fn in ("new_id", "create", "get", "append_step", "steps_for", "set_status", "list_by_status"):
        assert callable(getattr(agent_runs, fn))


def test_new_id_is_unique_and_urlsafe() -> None:
    from aeo.storage.repos import agent_runs

    ids = {agent_runs.new_id() for _ in range(200)}
    assert len(ids) == 200
    allowed = set(string.ascii_letters + string.digits + "-_")
    assert all(set(i) <= allowed and len(i) >= 8 for i in ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_runs_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: aeo.storage.repos.agent_runs`.

- [ ] **Step 3: Write the repo**

```python
# src/aeo/storage/repos/agent_runs.py
"""agent_runs / agent_steps — durable state for the Phase 2 agent runtime.

A run is the resumable artifact behind one assistive-copilot pass: the Planner stages a
task graph, a human approves/rejects it. Identity is a minted token (:func:`new_id`).
``idempotency_key`` (optional, UNIQUE) collapses duplicate enqueues. Like the other repos,
every function only touches the DB at call time via ``transaction()``.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from ..db import transaction

_TOKEN_BYTES = 9  # ~12 url-safe chars, matches plan_state ids


def new_id() -> str:
    """A fresh, unguessable agent-run id."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def create(
    *,
    idempotency_key: str | None = None,
    domain: str | None = None,
    client_id: int | None = None,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a new run (status 'queued') and return its row. When ``idempotency_key`` is
    set and already exists, return the existing run instead (dedupe). A NULL key never
    dedupes (Postgres treats NULLs as distinct)."""
    rid = new_id()
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_runs (id, idempotency_key, domain, client_id, brief)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (rid, idempotency_key, domain, client_id, json.dumps(brief or {}, default=str)),
        )
        row = cur.fetchone()
        if row is not None:
            return dict(row)
        cur.execute("SELECT * FROM agent_runs WHERE idempotency_key = %s", (idempotency_key,))
        return dict(cur.fetchone())


def get(run_id: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def set_status(
    run_id: str,
    status: str,
    *,
    current_step: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    """Advance a run's status. Optional fields are written only when provided, so a
    transition never clobbers an existing result/error with NULL."""
    sets = ["status = %s"]
    params: list[Any] = [status]
    if current_step is not None:
        sets.append("current_step = %s")
        params.append(current_step)
    if result is not None:
        sets.append("result = %s::jsonb")
        params.append(json.dumps(result, default=str))
    if error is not None:
        sets.append("error = %s")
        params.append(error)
    params.append(run_id)
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id = %s", tuple(params))
        return cur.rowcount > 0


def append_step(
    run_id: str,
    *,
    seq: int,
    agent: str,
    tool: str | None = None,
    status: str = "ok",
    model: str | None = None,
    tokens: int | None = None,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
    error_class: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_steps
                (run_id, seq, agent, tool, status, model, tokens, cost_usd, latency_ms, error_class, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (run_id, seq, agent, tool, status, model, tokens, cost_usd, latency_ms,
             error_class, json.dumps(detail or {}, default=str)),
        )
        return cur.fetchone()["id"]


def steps_for(run_id: str) -> list[dict[str, Any]]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_steps WHERE run_id = %s ORDER BY seq", (run_id,))
        return [dict(row) for row in cur.fetchall()]


def list_by_status(status: str, limit: int = 50) -> list[dict[str, Any]]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM agent_runs WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
            (status, limit),
        )
        return [dict(row) for row in cur.fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_runs_repo.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/storage/repos/agent_runs.py tests/unit/test_agent_runs_repo.py
git commit -m "feat(agents): agent_runs repo (create/step/status, idempotent)"
```

---

### Task 4: Live-DB round-trip (integration)

**Files:**
- Test: `tests/integration/test_agent_runs_db.py`

> Requires a reachable Postgres (`AEO__DATABASE__URL` / `DATABASE_URL`, default `postgresql://aeo:aeo@localhost:5432/aeo`) with migrations applied. This is the same live-DB contract as `tests/integration/test_db_smoke.py`.

- [ ] **Step 1: Apply migrations against the dev DB**

Run: `python -m aeo.cli migrate`
Expected: log line `migration_applied version=0019 name=agent_runs` (or `migrations_up_to_date` if already applied).

- [ ] **Step 2: Write the integration test**

```python
# tests/integration/test_agent_runs_db.py
"""Live-DB round-trip for the agent_runs repo. Skips cleanly when no DB is reachable."""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check
from aeo.storage.repos import agent_runs

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")


def test_create_step_status_round_trip() -> None:
    row = agent_runs.create(domain="acme.example", brief={"name": "Acme", "domain": "acme.example"})
    rid = row["id"]
    assert row["status"] == "queued"

    agent_runs.set_status(rid, "planning", current_step="plan")
    step_id = agent_runs.append_step(rid, seq=1, agent="planner", tool="plan_from_brief",
                                     detail={"task_count": 3})
    assert step_id > 0

    agent_runs.set_status(rid, "staged", current_step="review", result={"tasks": [1, 2, 3]})
    fetched = agent_runs.get(rid)
    assert fetched["status"] == "staged"
    assert fetched["result"] == {"tasks": [1, 2, 3]}

    steps = agent_runs.steps_for(rid)
    assert [s["agent"] for s in steps] == ["planner"]
    assert any(r["id"] == rid for r in agent_runs.list_by_status("staged"))


def test_idempotency_key_dedupes() -> None:
    a = agent_runs.create(idempotency_key="dedupe-key-xyz", brief={"name": "A"})
    b = agent_runs.create(idempotency_key="dedupe-key-xyz", brief={"name": "A"})
    assert a["id"] == b["id"]  # same run returned, not a second row
```

- [ ] **Step 3: Run the integration test**

Run: `python -m pytest tests/integration/test_agent_runs_db.py -v`
Expected: 2 passed (or skipped if no DB reachable).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_agent_runs_db.py
git commit -m "test(agents): live-DB round-trip for agent_runs repo"
```

---

### Task 5: Deterministic Planner

**Files:**
- Create: `src/aeo/agents/__init__.py`
- Create: `src/aeo/agents/planner.py`
- Test: `tests/unit/test_agent_planner.py`

- [ ] **Step 1: Write the failing test** (offline — `conftest.py` forces `AEO__LLM__ENABLED=false`, so the deterministic floor runs)

```python
# tests/unit/test_agent_planner.py
"""Planner: a deterministic agent task graph from a no-website brief (LLM disabled)."""

from __future__ import annotations

from aeo.agents.planner import plan_tasks
from aeo.reference.business_input import BusinessInput


def test_plan_tasks_returns_a_nonempty_deterministic_graph() -> None:
    brief = BusinessInput(name="Acme", domain="acme.com", topic="ctem")
    graph = plan_tasks(brief)  # llm defaults to None → deterministic floor

    assert graph["domain"] == "acme.com"
    assert graph["scenario"] == "no_website"
    assert graph["blueprint_pages"] > 0
    assert graph["tasks"], "expected at least one staged task"
    first = graph["tasks"][0]
    assert set(first) >= {"id", "kind", "title", "slug", "priority", "status"}
    assert first["status"] == "proposed"


def test_plan_tasks_is_stable() -> None:
    brief = BusinessInput(name="Acme", domain="acme.com", topic="ctem")
    a = plan_tasks(brief)
    b = plan_tasks(brief)
    assert [t["id"] for t in a["tasks"]] == [t["id"] for t in b["tasks"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: aeo.agents.planner`.

- [ ] **Step 3: Create the package marker**

```python
# src/aeo/agents/__init__.py
"""Phase 2 agent runtime: a deterministic controller that wraps the existing engines as
tools and stages proposals for human approval. See docs/superpowers/specs/2026-06-22-*."""

from .planner import plan_tasks
from .runtime import AgentRunController, start_agent_run

__all__ = ["AgentRunController", "plan_tasks", "start_agent_run"]
```

> Note: `runtime` is created in Task 6; this re-export will resolve once that file exists. If you run Task 5's test before Task 6, temporarily export only `plan_tasks` and add the rest in Task 6.

- [ ] **Step 4: Write the Planner**

```python
# src/aeo/agents/planner.py
"""Planner agent — turns a business brief into a deterministic agent task graph.

It wraps the existing deterministic planning seams (resolve_framework + plan_from_brief),
which always produce a versioned blueprint and a routed strategy with the LLM disabled. The
task graph is one 'create page' task per ideal blueprint node, ordered by node priority —
the work the Builder agent (Plan 2B) will later draft. Pure: no DB, LLM optional.
"""

from __future__ import annotations

from typing import Any

from ..intelligence.brief import plan_from_brief
from ..nlp.llm import LLMClient
from ..reference.business_input import BusinessInput
from ..reference.framework_bootstrap import resolve_framework


def plan_tasks(brief: BusinessInput, *, llm: LLMClient | None = None) -> dict[str, Any]:
    """A deterministic task graph for one assistive-copilot run.

    ``llm`` is optional; when None (or disabled) the deterministic floor still yields a full
    blueprint + plan, so this is safe to call offline. The returned dict is JSONB-serializable
    and is what gets staged on ``agent_runs.result`` for human review."""
    framework = resolve_framework(
        brief.key(), llm=llm, topic=brief.topic_hint(), category=brief.category
    )
    plan = plan_from_brief(brief, framework=framework, llm=llm)
    plan_d = plan.to_dict()
    profile = plan_d["profile"]
    nodes = plan_d["blueprint"]["sitemap"]

    tasks = [
        {
            "id": f"page:{n['slug']}",
            "kind": "content",
            "title": f"Create: {n['title']}",
            "slug": n["slug"],
            "page_type": n["page_type"],
            "priority": n["priority"],
            "status": "proposed",
        }
        for n in sorted(nodes, key=lambda n: n.get("priority", 999))
    ]

    return {
        "domain": brief.key(),
        "scenario": profile.get("scenario"),
        "headline": profile.get("headline"),
        "blueprint_pages": plan_d["blueprint"]["ideal_pages"],
        "coverage_pct": plan_d["coverage"]["pct"],
        "tasks": tasks,
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_planner.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/aeo/agents/__init__.py src/aeo/agents/planner.py tests/unit/test_agent_planner.py
git commit -m "feat(agents): deterministic Planner task graph"
```

---

### Task 6: AgentRunController runtime

**Files:**
- Create: `src/aeo/agents/runtime.py`
- Test: `tests/unit/test_agent_runtime.py`

- [ ] **Step 1: Write the failing test** (offline — inject a fake in-memory repo + fake planner; no DB)

```python
# tests/unit/test_agent_runtime.py
"""AgentRunController: plan→staged transitions and the failure path, via injected fakes."""

from __future__ import annotations

import pytest


class FakeRepo:
    """In-memory stand-in for storage.repos.agent_runs (only the methods the controller uses)."""

    def __init__(self, run: dict) -> None:
        self.runs = {run["id"]: dict(run)}
        self.steps: list[dict] = []

    def get(self, run_id):
        r = self.runs.get(run_id)
        return dict(r) if r else None

    def set_status(self, run_id, status, *, current_step=None, result=None, error=None):
        r = self.runs[run_id]
        r["status"] = status
        if current_step is not None:
            r["current_step"] = current_step
        if result is not None:
            r["result"] = result
        if error is not None:
            r["error"] = error
        return True

    def append_step(self, run_id, **kw):
        self.steps.append({"run_id": run_id, **kw})
        return len(self.steps)


def _row(brief=None, status="queued"):
    return {"id": "run1", "status": status, "brief": brief or {"name": "Acme", "domain": "acme.com"}}


def test_run_plans_and_stages() -> None:
    from aeo.agents.runtime import AgentRunController

    repo = FakeRepo(_row())
    graph = {"tasks": [{"id": "page:home"}, {"id": "page:about"}]}
    ctrl = AgentRunController(planner=lambda brief: graph, repo=repo)

    out = ctrl.run("run1")
    assert out["status"] == "staged"
    assert out["result"] == graph
    assert repo.steps == [
        {"run_id": "run1", "seq": 1, "agent": "planner", "tool": "plan_from_brief",
         "status": "ok", "detail": {"task_count": 2}}
    ]


def test_run_records_failure_and_reraises() -> None:
    from aeo.agents.runtime import AgentRunController

    repo = FakeRepo(_row())

    def boom(brief):
        raise RuntimeError("planner exploded")

    ctrl = AgentRunController(planner=boom, repo=repo)
    with pytest.raises(RuntimeError, match="planner exploded"):
        ctrl.run("run1")

    assert repo.runs["run1"]["status"] == "failed"
    assert repo.steps[0]["status"] == "failed"
    assert repo.steps[0]["error_class"] == "RuntimeError"


def test_run_is_noop_on_terminal_status() -> None:
    from aeo.agents.runtime import AgentRunController

    repo = FakeRepo(_row(status="approved"))
    called = []
    ctrl = AgentRunController(planner=lambda brief: called.append(1) or {}, repo=repo)
    out = ctrl.run("run1")
    assert out["status"] == "approved"
    assert called == []  # already resolved → the planner never runs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: aeo.agents.runtime`.

- [ ] **Step 3: Write the runtime**

```python
# src/aeo/agents/runtime.py
"""AgentRunController — the deterministic controller for one assistive-copilot run.

It is the agent-era sibling of orchestrator.audit_cycle: it sequences typed steps, persists
each to agent_steps, and leaves the run 'staged' for human approval. Every step has a
deterministic floor, so an LLM failure (Phase 2B) degrades to the deterministic result rather
than blocking. Injectable (planner/repo/brief_builder) so the loop is unit-testable with no DB.
"""

from __future__ import annotations

from typing import Any

from ..reference.business_input import BusinessInput
from ..storage.repos import agent_runs as agent_runs_repo
from .planner import plan_tasks

# A run in one of these states is already resolved — re-delivery of its job is a safe no-op.
_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})


def brief_from_dict(d: dict[str, Any]) -> BusinessInput:
    """Rebuild the BusinessInput the run was seeded with (stored on agent_runs.brief)."""
    return BusinessInput(
        name=d.get("name") or d.get("domain") or "site",
        domain=d.get("domain"),
        category=d.get("category"),
        topic=d.get("topic"),
        location=d.get("location"),
        services=list(d.get("services") or []),
        competitors=list(d.get("competitors") or []),
        goals=list(d.get("goals") or []),
    )


class AgentRunController:
    def __init__(self, *, planner=plan_tasks, repo=agent_runs_repo, brief_builder=brief_from_dict) -> None:
        self._planner = planner
        self._repo = repo
        self._brief = brief_builder

    def run(self, run_id: str) -> dict[str, Any]:
        """Drive a run from 'queued' to 'staged'. Idempotent: a run already in a terminal
        state is returned unchanged (safe under at-least-once job delivery)."""
        row = self._repo.get(run_id)
        if row is None:
            raise ValueError(f"unknown agent run: {run_id!r}")
        if row["status"] in _TERMINAL:
            return row

        self._repo.set_status(run_id, "planning", current_step="plan")
        brief = self._brief(row.get("brief") or {})
        try:
            graph = self._planner(brief)
        except Exception as exc:
            self._repo.append_step(
                run_id, seq=1, agent="planner", tool="plan_from_brief", status="failed",
                error_class=type(exc).__name__, detail={"error": str(exc)},
            )
            self._repo.set_status(run_id, "failed", error=str(exc))
            raise

        self._repo.append_step(
            run_id, seq=1, agent="planner", tool="plan_from_brief", status="ok",
            detail={"task_count": len(graph.get("tasks", []))},
        )
        self._repo.set_status(run_id, "staged", current_step="review", result=graph)
        return self._repo.get(run_id)


def start_agent_run(brief: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
    """Create a run row and enqueue it on the Postgres job queue for a worker to drive.
    Used by the API and CLI. The worker picks it up via the AGENT_RUN job kind."""
    from ..pipeline.worker import enqueue_agent_run  # lazy: avoid import cycle with worker

    row = agent_runs_repo.create(
        idempotency_key=idempotency_key, domain=brief.get("domain"), client_id=None, brief=brief
    )
    enqueue_agent_run(row["id"])
    return row
```

> The failure test injects a fake repo whose `append_step` ignores extra kwargs (`**kw`), so the `error_class`/`detail` keys are captured — matching the assertions.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_runtime.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/agents/runtime.py tests/unit/test_agent_runtime.py
git commit -m "feat(agents): AgentRunController (plan->staged loop, DI-tested)"
```

---

### Task 7: Worker wiring — AGENT_RUN job kind

**Files:**
- Modify: `src/aeo/pipeline/worker.py`
- Test: `tests/unit/test_agent_worker.py`

- [ ] **Step 1: Write the failing test** (offline — monkeypatch the controller; no DB)

```python
# tests/unit/test_agent_worker.py
"""Worker dispatches the AGENT_RUN job kind to the AgentRunController."""

from __future__ import annotations


def test_enqueue_agent_run_uses_the_db_queue(monkeypatch) -> None:
    from aeo.pipeline import worker as worker_mod
    from aeo.storage.repos import jobs as jobs_repo

    seen = {}

    def fake_enqueue(kind, payload, run_after=None, max_attempts=4):
        seen.update(kind=kind, payload=payload, max_attempts=max_attempts)
        return 99

    monkeypatch.setattr(jobs_repo, "enqueue", fake_enqueue)
    job_id = worker_mod.enqueue_agent_run("run-abc")
    assert job_id == 99
    assert seen["kind"] == worker_mod.AGENT_RUN
    assert seen["payload"] == {"run_id": "run-abc"}


def test_dispatch_routes_agent_run_to_controller(monkeypatch) -> None:
    from aeo.agents import runtime as runtime_mod
    from aeo.pipeline.worker import AGENT_RUN, Worker

    ran = {}

    class FakeController:
        def run(self, run_id):
            ran["run_id"] = run_id
            return {"id": run_id, "status": "staged"}

    monkeypatch.setattr(runtime_mod, "AgentRunController", FakeController)
    Worker()._dispatch({"kind": AGENT_RUN, "payload": {"run_id": "run-xyz"}})
    assert ran["run_id"] == "run-xyz"


def test_default_kinds_include_agent_run() -> None:
    from aeo.pipeline.worker import AGENT_RUN, Worker

    assert AGENT_RUN in Worker().kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_worker.py -v`
Expected: FAIL with `AttributeError: module 'aeo.pipeline.worker' has no attribute 'enqueue_agent_run'`.

- [ ] **Step 3: Add the AGENT_RUN constant + enqueue helper**

In `src/aeo/pipeline/worker.py`, below the existing `ANALYZE_RUN = "analyze_run"` line:

```python
AGENT_RUN = "agent_run"
```

Add this enqueue helper after `enqueue_analysis(...)`:

```python
def enqueue_agent_run(run_id: str, max_attempts: int = 3) -> int:
    """Enqueue an assistive agent run (AgentRunController.run) for a worker to drive."""
    return jobs_repo.enqueue(AGENT_RUN, {"run_id": run_id}, max_attempts=max_attempts)
```

- [ ] **Step 4: Add AGENT_RUN to the default kinds and dispatch**

Change the `Worker.__init__` default kinds line from:

```python
        self.kinds = kinds or [CRAWL_BATCH, ANALYZE_RUN]
```

to:

```python
        self.kinds = kinds or [CRAWL_BATCH, ANALYZE_RUN, AGENT_RUN]
```

In `Worker._dispatch`, add a branch before the final `else`:

```python
        elif kind == AGENT_RUN:
            from ..agents.runtime import AgentRunController  # lazy: avoid import cycle
            AgentRunController().run(str(payload["run_id"]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_worker.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full agent unit suite to confirm no regressions**

Run: `python -m pytest tests/unit/test_agent_planner.py tests/unit/test_agent_runtime.py tests/unit/test_agent_worker.py tests/unit/test_agents_settings.py -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add src/aeo/pipeline/worker.py tests/unit/test_agent_worker.py
git commit -m "feat(agents): AGENT_RUN job kind + worker dispatch"
```

---

### Task 8: API endpoints — start / status / approve / reject

**Files:**
- Modify: `src/aeo/api/app.py`
- Test: `tests/unit/test_agent_api.py`

- [ ] **Step 1: Write the failing test** (offline — `TestClient` + monkeypatch repo/service; no DB, no DNS since the brief has no domain)

```python
# tests/unit/test_agent_api.py
"""Agent-run endpoints — wiring over the repo/service (monkeypatched, no DB)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from aeo.api.app import app

client = TestClient(app)


def test_start_returns_run_id(monkeypatch) -> None:
    from aeo.agents import runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "start_agent_run",
                        lambda brief, **kw: {"id": "run42", "status": "queued"})
    r = client.post("/api/agent/run", json={"name": "Acme", "topic": "ctem"})
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "queued"}


def test_status_returns_run_and_steps(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged",
                                                  "result": {"tasks": []}, "domain": "acme.com"})
    monkeypatch.setattr(repo, "steps_for", lambda rid: [{"seq": 1, "agent": "planner"}])
    body = client.get("/api/agent/run/run42").json()
    assert body["status"] == "staged"
    assert body["steps"][0]["agent"] == "planner"


def test_status_404_for_unknown_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: None)
    assert client.get("/api/agent/run/nope").status_code == 404


def test_approve_flips_a_staged_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    captured = {}
    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: captured.update(rid=rid, status=status))
    r = client.post("/api/agent/run/run42/approve")
    assert r.status_code == 200
    assert r.json() == {"run_id": "run42", "status": "approved"}
    assert captured == {"rid": "run42", "status": "approved"}


def test_reject_flips_a_staged_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged"})
    monkeypatch.setattr(repo, "set_status", lambda rid, status, **kw: None)
    r = client.post("/api/agent/run/run42/reject")
    assert r.json() == {"run_id": "run42", "status": "rejected"}


def test_approve_409_when_not_staged(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "approved"})
    assert client.post("/api/agent/run/run42/approve").status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_api.py -v`
Expected: FAIL — `POST /api/agent/run` returns 404 (route not defined yet).

- [ ] **Step 3: Add the endpoints**

In `src/aeo/api/app.py`, add this block in the `# ── endpoints ──` section (e.g. after the `/api/audit` handlers). It reuses the existing `BriefRequest` model, `_brief()` helper, and `_assert_crawlable_host` guard:

```python
# ── agent runs (Phase 2A: assistive copilot + human approval gate) ──────────────


def _decide_agent_run(run_id: str, decision: str) -> dict[str, Any]:
    """Approve/reject gate: only a 'staged' run can be decided, and only a human does it."""
    from ..storage.repos import agent_runs as agent_runs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    if row["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"run is {row['status']}, not staged")
    agent_runs_repo.set_status(run_id, decision)
    return {"run_id": run_id, "status": decision}


@app.post("/api/agent/run")
def agent_run_start(req: BriefRequest) -> dict[str, Any]:
    """Start an assistive agent run. The Planner stages a task graph for human review; nothing
    is published. Returns the run id to poll."""
    if req.domain:
        _assert_crawlable_host(req.domain)  # SSRF parity — the run may crawl this domain later
    from ..agents.runtime import start_agent_run

    row = start_agent_run(_brief(req).to_dict())
    return {"run_id": row["id"], "status": row["status"]}


@app.get("/api/agent/run/{run_id}")
def agent_run_status(run_id: str) -> dict[str, Any]:
    """The run's status + its per-step trace (the staged task graph is in ``result``)."""
    from ..storage.repos import agent_runs as agent_runs_repo

    row = agent_runs_repo.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown agent run")
    return {**row, "steps": agent_runs_repo.steps_for(run_id)}


@app.post("/api/agent/run/{run_id}/approve")
def agent_run_approve(run_id: str) -> dict[str, Any]:
    return _decide_agent_run(run_id, "approved")


@app.post("/api/agent/run/{run_id}/reject")
def agent_run_reject(run_id: str) -> dict[str, Any]:
    return _decide_agent_run(run_id, "rejected")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_api.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the existing API suite to confirm no regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: all passed (no existing endpoint changed).

- [ ] **Step 6: Commit**

```bash
git add src/aeo/api/app.py tests/unit/test_agent_api.py
git commit -m "feat(agents): /api/agent/run start/status/approve/reject"
```

---

### Task 9: CLI command + end-to-end verification

**Files:**
- Modify: `src/aeo/cli.py`
- Test: manual end-to-end (commands below)

- [ ] **Step 1: Add the `aeo agent` command**

In `src/aeo/cli.py`, add a command alongside the others (after the `plan` command). It mirrors the existing `_bootstrap()` boot pattern:

```python
@app.command()
def agent(
    name: str = typer.Argument(..., help="Business name, e.g. Acme"),
    domain: str | None = typer.Option(None, "--domain", "-d", help="Domain, e.g. acme.com"),
    topic: str | None = typer.Option(None, "--topic", "-t", help="Topic hint, e.g. ctem"),
    category: str | None = typer.Option(None, "--category", "-c", help="Industry/category"),
) -> None:
    """Enqueue an assistive agent run (Planner stages a task graph for human review)."""
    _bootstrap()
    from .agents.runtime import start_agent_run
    from .reference.business_input import BusinessInput

    brief = BusinessInput(name=name, domain=domain, topic=topic, category=category).to_dict()
    row = start_agent_run(brief)
    typer.echo(f"agent run {row['id']} queued (status={row['status']}). Run `aeo worker` to process it.")
```

- [ ] **Step 2: Verify the CLI wiring loads (no DB needed for --help)**

Run: `python -m aeo.cli agent --help`
Expected: usage text listing `name`, `--domain`, `--topic`, `--category`.

- [ ] **Step 3: End-to-end smoke against the live DB**

Requires Postgres up + migrations applied (Task 4 Step 1). In one shell:

Run: `python -m aeo.cli agent Acme --domain acme.com --topic ctem`
Expected: `agent run <id> queued (status=queued). Run \`aeo worker\` to process it.`

In a second shell, drain one job:

Run: `python -m aeo.cli worker` *(Ctrl-C after it logs `job_done ... kind=agent_run`)*
Expected: a `job_done kind=agent_run` log line.

Then poll the run (replace `<id>`):

Run: `python -c "import json; from aeo.storage.repos import agent_runs as r; print(json.dumps(r.get('<id>'), default=str, indent=2))"`
Expected: `"status": "staged"` with a non-empty `result.tasks` array.

- [ ] **Step 4: Run the whole test suite + linter**

Run: `python -m pytest -q`
Expected: all green (the existing suite + the new agent tests).

Run: `python -m ruff check src/aeo/agents src/aeo/api/app.py src/aeo/pipeline/worker.py src/aeo/cli.py src/aeo/settings.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/aeo/cli.py
git commit -m "feat(agents): aeo agent CLI command + e2e wiring"
```

---

## Self-Review

**Spec coverage (against §2 of the design doc):** the runtime, durable `agent_runs`/`agent_steps` state, the deterministic Planner mapped to existing seams, worker-queue scheduling (no new broker), and the human approval gate are all implemented. State keys on `domain`/`url_normalized` (never `page_id`) — honored: no `page_id` appears here. The `idempotency_key` column + UNIQUE constraint ship now; the API passes `None` (no dedupe) deliberately — wiring a real per-request dedupe token is the first task of Plan 2B, where the Builder introduces real cost and double-runs would double-bill. Hybrid LLM routing, the Builder/Research agents, the Critic pipeline, SSE, and the frontend `AgentReviewQueue.tsx` are explicitly deferred to Plans 2B/2C (stated in Scope).

**Placeholder scan:** none — every code step shows complete code; every test step shows the assertions; every run step shows the command + expected output.

**Type/name consistency:** `plan_tasks(brief, *, llm=None)`, `AgentRunController(planner, repo, brief_builder).run(run_id)`, `start_agent_run(brief, *, idempotency_key=None)`, `enqueue_agent_run(run_id, max_attempts=3)`, `AGENT_RUN = "agent_run"`, and the repo signatures (`create`/`get`/`append_step`/`steps_for`/`set_status`/`list_by_status`) are used identically across the runtime, worker, API, CLI, and tests. The `agent_runs.result` JSONB holds the Planner's task graph; the status enum (`queued→planning→staged→approved/rejected/failed/cancelled`) is consistent between the migration CHECK constraint, the controller's `_TERMINAL` set, and the API's 409 guard.

**Known follow-ups (not gaps in this slice):** the in-memory `JobRegistry` still backs audits (Phase 4 replaces it); per-step cancellation/timeouts are config-present (`AgentsCfg.step_timeout_sec`) but enforced in 2B; observability `cost_usd`/`tokens` columns exist but are populated by the 2B LLM router.
