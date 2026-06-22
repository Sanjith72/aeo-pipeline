# Gamified Companion — Engine + State + UI (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the honest core of the ATLAS gamification system — rewards that are granted ONLY from real, re-crawl-verified outcomes, never from activity. The engine reconciles verified wins and AEO-score milestones into a per-session state + an idempotent award ledger, exposes them over a small API, and renders a restrained `GamificationStrip` (band, verified-win count, maturity) reusing the existing motion vocabulary.

**Architecture:** A reward reconciler (`companion/rewards.py`) reads the existing verdict tables — primarily `outcomes.implemented_for_domain` (an outcome flips to `implemented` only when a re-crawled criterion's tier rises) — and grants awards idempotently into `gamification_awards` (UNIQUE on the source verdict row, so re-running never double-grants). Derived state (maturity stage, band, momentum, counts) lives in `gamification_state`, keyed on the auth-free `session_id`. A new `GET /api/gamification` + `POST /api/gamification/reconcile` surface it; the frontend renders it. No vanity currency: the canonical `aeoScore` (web/lib/score.ts) stays the headline; momentum only moves on verified outcomes.

**Tech Stack:** Backend: Python 3.11, psycopg2, FastAPI, pytest (matches the agent plans). Frontend: Next.js 15 / React 19 / framer-motion 12 / TS; node:test for pure fns; `next build` for components.

---

## Prerequisite / context

This rides existing tables read-only: `recommendation_outcomes` (the Verified-live moat, migration 0010), `crawl_runs` (0001), `clients` (0001). The canonical score is computed frontend by `aeoScore(profile)` (`web/lib/score.ts`); the reconcile endpoint accepts it as input so the backend never invents a number. Migration numbering: Plan 2A used 0019; 2B/2C added none; this plan uses **0020, 0021, 0022**.

## Scope

**In scope (the honest engine):** the three gamification migrations, the `gamification` repo, the reward reconciler (verified wins + AEO-score achievements), the `/api/gamification` endpoints, and a frontend client + pure maturity helper + `GamificationStrip` component.

**Out of scope — Plan 3B (the conversational companion):** the ATLAS narrator prose (`companion/narrator.py` turning agent steps into narration), the LLM-phrased coach (`companion/coach.py`), the full `CompanionRail` UI, and the citations-earned reward axis (needs the `citation_results` schema wired). Those are a larger follow-on; this plan ships the reward backbone they sit on. The maturity ladder here tops out at `authority`; `cited_leader` (requires a citation) lands with 3B.

**Honesty invariants (do not violate):** every award joins to a real verdict row; awards are idempotent on `(award_type, source_table, source_id)`; momentum/wins only move on `implemented` outcomes; NEVER grant on a manual task toggle; the AEO Score stays the single headline number.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/aeo/storage/migrations/0020_gamification_state.sql` | Create | `gamification_state` (derived per-session state). |
| `src/aeo/storage/migrations/0021_gamification_awards.sql` | Create | `gamification_awards` (idempotent ledger). |
| `src/aeo/storage/migrations/0022_achievements.sql` | Create | `achievement_definitions` + `achievement_unlocks` + seed. |
| `src/aeo/storage/repos/gamification.py` | Create | `get_state`, `upsert_state`, `grant_award`, `awards_for`, `unlock_achievement`. |
| `src/aeo/companion/__init__.py` | Create | Package marker. |
| `src/aeo/companion/rewards.py` | Create | `reconcile(session_id, domain, aeo_score)` + pure `band`/`maturity`. |
| `src/aeo/api/app.py` | Modify | `GET /api/gamification` + `POST /api/gamification/reconcile`. |
| `web/lib/types.ts` | Modify | `GamificationState`, `GamificationAward`, `GamificationView`. |
| `web/lib/api.ts` | Modify | `getGamification`, `reconcileGamification`. |
| `web/lib/gamify.ts` | Create | `MATURITY_ORDER`/`MATURITY_LABEL`/`maturityProgress` (pure). |
| `web/lib/gamify.test.ts` | Create | node:test for the maturity helpers. |
| `web/components/GamificationStrip.tsx` | Create | The restrained status strip (band + wins + maturity). |
| `tests/unit/test_gamification_schema.py` | Create | Offline migration assertions. |
| `tests/unit/test_gamification_repo.py` | Create | Repo API existence (offline). |
| `tests/unit/test_rewards.py` | Create | Pure `band`/`maturity`; reconcile via injected fakes. |
| `tests/unit/test_gamification_api.py` | Create | Endpoint wiring (monkeypatched). |
| `tests/integration/test_gamification_db.py` | Create | Live-DB state + award round-trip. |

**Run** backend tests with `python -m pytest`; frontend pure-fn tests from `web/` with `npm test`; frontend build with `npm run build`.

---

### Task 1: Migrations 0020–0022

**Files:**
- Create: `src/aeo/storage/migrations/0020_gamification_state.sql`, `0021_gamification_awards.sql`, `0022_achievements.sql`
- Test: `tests/unit/test_gamification_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gamification_schema.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_gamification_schema.py -v`
Expected: FAIL on `migration 0020 not discovered`.

- [ ] **Step 3: Write `0020_gamification_state.sql`**

```sql
-- Phase 3 gamification: derived per-session companion state. Reads verdicts from existing
-- tables (recommendation_outcomes, etc.); persists only derived state. Keyed on session_id
-- (the auth-free DAU identity, like events/plan_states). Additive + idempotent.

CREATE TABLE IF NOT EXISTS gamification_state (
    session_id        TEXT        PRIMARY KEY,
    client_id         INTEGER     REFERENCES clients(id) ON DELETE SET NULL,
    domain            TEXT,
    maturity_stage    VARCHAR(20) NOT NULL DEFAULT 'foundations'
        CHECK (maturity_stage IN ('foundations','on_radar','recommended','authority','cited_leader')),
    aeo_score         INTEGER,
    aeo_band          VARCHAR(20),
    momentum          INTEGER     NOT NULL DEFAULT 0,
    last_verified_at  TIMESTAMPTZ,
    verified_wins     INTEGER     NOT NULL DEFAULT 0,
    citations_earned  INTEGER     NOT NULL DEFAULT 0,
    track_progress    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gam_state_domain ON gamification_state (domain);

DROP TRIGGER IF EXISTS trg_gam_state_updated_at ON gamification_state;
CREATE TRIGGER trg_gam_state_updated_at
    BEFORE UPDATE ON gamification_state
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

- [ ] **Step 4: Write `0021_gamification_awards.sql`**

```sql
-- Append-only award ledger. UNIQUE(award_type, source_table, source_id) is the anti-inflation
-- guard: reconcile re-runs never double-grant an award sourced from the same verdict row. Every
-- award comes from a real verdict (source_id NOT NULL), so the count can never drift from reality.

CREATE TABLE IF NOT EXISTS gamification_awards (
    id            BIGSERIAL    PRIMARY KEY,
    session_id    TEXT         NOT NULL,
    client_id     INTEGER      REFERENCES clients(id) ON DELETE SET NULL,
    award_type    VARCHAR(30)  NOT NULL
        CHECK (award_type IN ('verified_win','citation','status_tier','maturity_up')),
    source_table  VARCHAR(40)  NOT NULL,
    source_id     BIGINT       NOT NULL,
    criterion     VARCHAR(40),
    tier_before   SMALLINT,
    tier_after    SMALLINT,
    score_delta   INTEGER,
    detail        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (award_type, source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_gam_awards_session ON gamification_awards (session_id, created_at DESC);
```

- [ ] **Step 5: Write `0022_achievements.sql`**

```sql
-- Status-tier credentials (GitHub/Linear flavored), declaratively bound to real metrics and
-- earned once per session. Seeded with the two AEO-score milestones; more land with Plan 3B.

CREATE TABLE IF NOT EXISTS achievement_definitions (
    code             VARCHAR(40)  PRIMARY KEY,
    title            VARCHAR(120) NOT NULL,
    description      TEXT,
    unlock_metric    VARCHAR(40)  NOT NULL,   -- 'aeo_score' | 'coverage_pct' | 'citation_count'
    unlock_threshold NUMERIC      NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS achievement_unlocks (
    id            BIGSERIAL   PRIMARY KEY,
    session_id    TEXT        NOT NULL,
    code          VARCHAR(40) NOT NULL REFERENCES achievement_definitions(code),
    unlocked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_run_id INTEGER     REFERENCES crawl_runs(id) ON DELETE SET NULL,
    UNIQUE (session_id, code)
);

INSERT INTO achievement_definitions (code, title, description, unlock_metric, unlock_threshold) VALUES
    ('recommended', 'Recommended', 'Your AEO Score crossed into Recommended.', 'aeo_score', 60),
    ('top_answer',  'Top Answer',  'Your AEO Score reached Top Answer.',       'aeo_score', 80)
ON CONFLICT (code) DO NOTHING;
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_gamification_schema.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/aeo/storage/migrations/0020_gamification_state.sql src/aeo/storage/migrations/0021_gamification_awards.sql src/aeo/storage/migrations/0022_achievements.sql tests/unit/test_gamification_schema.py
git commit -m "feat(gamification): migrations 0020-0022 (state, awards, achievements)"
```

---

### Task 2: gamification repo

**Files:**
- Create: `src/aeo/storage/repos/gamification.py`
- Test: `tests/unit/test_gamification_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gamification_repo.py
"""gamification repo — offline API existence (DB round-trip lives in integration)."""

from __future__ import annotations


def test_exposes_its_api() -> None:
    from aeo.storage.repos import gamification

    for fn in ("get_state", "upsert_state", "grant_award", "awards_for", "unlock_achievement"):
        assert callable(getattr(gamification, fn))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_gamification_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the repo**

```python
# src/aeo/storage/repos/gamification.py
"""gamification — derived companion state + an idempotent award ledger.

State is keyed on session_id (the auth-free DAU identity). Awards are append-only and unique
on (award_type, source_table, source_id), so reconcile re-runs never double-grant. The
reconciler (companion/rewards.py) is the brain; this module is dumb storage.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import transaction


def get_state(session_id: str) -> dict[str, Any] | None:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM gamification_state WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_state(
    session_id: str,
    *,
    domain: str | None = None,
    client_id: int | None = None,
    aeo_score: int | None = None,
    aeo_band: str | None = None,
    maturity_stage: str = "foundations",
    momentum: int = 0,
    verified_wins: int = 0,
    citations_earned: int = 0,
    track_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert or fully replace a session's derived state. The reconciler computes every field,
    so this is a straight upsert (no COALESCE games)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gamification_state
                (session_id, domain, client_id, aeo_score, aeo_band, maturity_stage,
                 momentum, verified_wins, citations_earned, track_progress)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (session_id) DO UPDATE SET
                domain           = EXCLUDED.domain,
                client_id        = EXCLUDED.client_id,
                aeo_score        = EXCLUDED.aeo_score,
                aeo_band         = EXCLUDED.aeo_band,
                maturity_stage   = EXCLUDED.maturity_stage,
                momentum         = EXCLUDED.momentum,
                verified_wins    = EXCLUDED.verified_wins,
                citations_earned = EXCLUDED.citations_earned,
                track_progress   = EXCLUDED.track_progress
            RETURNING *
            """,
            (session_id, domain, client_id, aeo_score, aeo_band, maturity_stage,
             momentum, verified_wins, citations_earned, json.dumps(track_progress or {}, default=str)),
        )
        return dict(cur.fetchone())


def grant_award(
    session_id: str,
    *,
    award_type: str,
    source_table: str,
    source_id: int,
    client_id: int | None = None,
    criterion: str | None = None,
    tier_before: int | None = None,
    tier_after: int | None = None,
    score_delta: int | None = None,
    detail: dict[str, Any] | None = None,
) -> int | None:
    """Grant an award idempotently. Returns the new award id, or None if it was already granted
    (the UNIQUE(award_type, source_table, source_id) guard fired)."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gamification_awards
                (session_id, client_id, award_type, source_table, source_id,
                 criterion, tier_before, tier_after, score_delta, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (award_type, source_table, source_id) DO NOTHING
            RETURNING id
            """,
            (session_id, client_id, award_type, source_table, source_id,
             criterion, tier_before, tier_after, score_delta, json.dumps(detail or {}, default=str)),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def awards_for(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM gamification_awards WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
            (session_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def unlock_achievement(session_id: str, code: str, *, source_run_id: int | None = None) -> bool:
    """Earn a status tier once. Returns True only on the first unlock for this session+code."""
    with transaction() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO achievement_unlocks (session_id, code, source_run_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (session_id, code) DO NOTHING RETURNING id",
            (session_id, code, source_run_id),
        )
        return cur.fetchone() is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_gamification_repo.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/storage/repos/gamification.py tests/unit/test_gamification_repo.py
git commit -m "feat(gamification): state + awards + achievements repo"
```

---

### Task 3: Reward reconciler

**Files:**
- Create: `src/aeo/companion/__init__.py`, `src/aeo/companion/rewards.py`
- Test: `tests/unit/test_rewards.py`

- [ ] **Step 1: Write the failing test** (pure helpers offline + reconcile via injected fakes)

```python
# tests/unit/test_rewards.py
"""Reward reconciler: pure band/maturity + idempotent reconcile over injected fakes."""

from __future__ import annotations

import pytest

from aeo.companion.rewards import band, maturity, reconcile


@pytest.mark.parametrize("score,expected", [(10, "Barely visible"), (50, "On the radar"),
                                            (70, "Recommended"), (90, "Top answer")])
def test_band(score, expected) -> None:
    assert band(score) == expected


@pytest.mark.parametrize("score,expected", [(10, "foundations"), (50, "on_radar"),
                                            (70, "recommended"), (90, "authority")])
def test_maturity(score, expected) -> None:
    assert maturity(score) == expected


class FakeGam:
    def __init__(self) -> None:
        self.awarded: list[int] = []
        self.unlocked: list[str] = []
        self.state: dict | None = None

    def grant_award(self, session_id, *, award_type, source_table, source_id, **kw):
        if source_id in self.awarded:
            return None  # idempotent: already granted
        self.awarded.append(source_id)
        return source_id

    def get_state(self, session_id):
        return self.state

    def unlock_achievement(self, session_id, code, **kw):
        if code in self.unlocked:
            return False
        self.unlocked.append(code)
        return True

    def upsert_state(self, session_id, **fields):
        self.state = {"session_id": session_id, **fields}
        return self.state


def test_reconcile_grants_each_verified_win_once_and_unlocks_tiers() -> None:
    gam = FakeGam()
    wins = [{"id": 1, "criterion": "qa_blocks", "url_normalized": "https://acme.com/a"},
            {"id": 2, "criterion": "schema_markup", "url_normalized": "https://acme.com/b"}]

    out = reconcile("s1", "acme.com", aeo_score=72, _gam=gam, _wins=wins)
    assert len(out["new_awards"]) == 2
    assert out["unlocked"] == ["recommended"]
    assert out["state"]["verified_wins"] == 2
    assert out["state"]["momentum"] == 2
    assert out["state"]["maturity_stage"] == "recommended"

    # Re-running grants nothing new (idempotent), momentum doesn't inflate past real wins.
    gam.state = {"momentum": 2}
    out2 = reconcile("s1", "acme.com", aeo_score=72, _gam=gam, _wins=wins)
    assert out2["new_awards"] == []
    assert out2["state"]["momentum"] == 2  # 2 prior + 0 new
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_rewards.py -v`
Expected: FAIL — `ModuleNotFoundError: aeo.companion.rewards`.

- [ ] **Step 3: Write the package marker + reconciler**

```python
# src/aeo/companion/__init__.py
"""ATLAS companion: honest gamification + (Plan 3B) narration/coaching over agent work."""
```

```python
# src/aeo/companion/rewards.py
"""Reward reconciler — grant gamification awards ONLY from real, re-crawl-verified outcomes.

Sources verified wins from outcomes.implemented_for_domain (an outcome flips to 'implemented'
only when a re-crawled criterion's tier rises — never on a manual toggle or a bare content
change), grants them idempotently, and derives per-session state. The AEO Score is passed in
(computed by the frontend's canonical formula), so the backend never invents a number.

Pure-ish + injectable (_gam/_wins) so the logic is unit-testable with no DB. Best-effort: a DB
hiccup must never break the app — callers wrap accordingly.
"""

from __future__ import annotations

from typing import Any


def band(score: int) -> str:
    """The AEO Score band label — mirrors web/lib/score.ts scoreBand()."""
    if score < 40:
        return "Barely visible"
    if score < 60:
        return "On the radar"
    if score < 80:
        return "Recommended"
    return "Top answer"


def maturity(score: int) -> str:
    """Maturity stage from the score. (cited_leader requires a citation — Plan 3B.)"""
    if score < 40:
        return "foundations"
    if score < 60:
        return "on_radar"
    if score < 80:
        return "recommended"
    return "authority"


# AEO-score achievement thresholds (code, min score). Mirrors the 0022 seed.
_SCORE_ACHIEVEMENTS = (("recommended", 60), ("top_answer", 80))


def reconcile(
    session_id: str,
    domain: str | None,
    *,
    aeo_score: int | None = None,
    _gam: Any = None,
    _wins: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Grant verified-win awards + score achievements, refresh state. Idempotent. ``_gam`` and
    ``_wins`` are injection seams for tests; in production they default to the real repos."""
    if _gam is None:
        from ..storage.repos import gamification as _gam  # noqa: PLC0415
    wins = _wins
    if wins is None:
        from ..storage.repos import outcomes as outcomes_repo  # noqa: PLC0415
        wins = outcomes_repo.implemented_for_domain(domain) if domain else []

    new_awards: list[dict[str, Any]] = []
    for w in wins:
        award_id = _gam.grant_award(
            session_id,
            award_type="verified_win",
            source_table="recommendation_outcomes",
            source_id=w["id"],
            criterion=w.get("criterion"),
            detail={"url": w.get("url_normalized"), "detected_at": str(w.get("detected_at"))},
        )
        if award_id is not None:
            new_awards.append({"award_id": award_id, "criterion": w.get("criterion"),
                               "url": w.get("url_normalized")})

    unlocked: list[str] = []
    if aeo_score is not None:
        for code, threshold in _SCORE_ACHIEVEMENTS:
            if aeo_score >= threshold and _gam.unlock_achievement(session_id, code):
                unlocked.append(code)

    prev = _gam.get_state(session_id) or {}
    momentum = int(prev.get("momentum", 0)) + len(new_awards)
    state = _gam.upsert_state(
        session_id,
        domain=domain,
        aeo_score=aeo_score,
        aeo_band=band(aeo_score) if aeo_score is not None else None,
        maturity_stage=maturity(aeo_score) if aeo_score is not None else "foundations",
        momentum=momentum,
        verified_wins=len(wins),
    )
    return {"new_awards": new_awards, "unlocked": unlocked, "state": state}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_rewards.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/companion/__init__.py src/aeo/companion/rewards.py tests/unit/test_rewards.py
git commit -m "feat(gamification): reward reconciler (verified-win awards + score tiers)"
```

---

### Task 4: API endpoints

**Files:**
- Modify: `src/aeo/api/app.py`
- Test: `tests/unit/test_gamification_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gamification_api.py
"""Gamification endpoints — wiring over the repo/reconciler (monkeypatched, no DB)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from aeo.api.app import app

client = TestClient(app)


def test_get_gamification_returns_state_and_awards(monkeypatch) -> None:
    from aeo.storage.repos import gamification as gam

    monkeypatch.setattr(gam, "get_state", lambda sid: {"session_id": sid, "verified_wins": 3})
    monkeypatch.setattr(gam, "awards_for", lambda sid, limit=50: [{"id": 1, "award_type": "verified_win"}])
    body = client.get("/api/gamification?session_id=s1&domain=acme.com").json()
    assert body["state"]["verified_wins"] == 3
    assert body["awards"][0]["award_type"] == "verified_win"


def test_get_gamification_empty_for_unknown_session(monkeypatch) -> None:
    from aeo.storage.repos import gamification as gam

    monkeypatch.setattr(gam, "get_state", lambda sid: None)
    body = client.get("/api/gamification?session_id=nope").json()
    assert body == {"state": None, "awards": []}


def test_reconcile_delegates_to_the_reconciler(monkeypatch) -> None:
    from aeo.companion import rewards

    monkeypatch.setattr(rewards, "reconcile",
                        lambda sid, domain, *, aeo_score=None: {"new_awards": [{"award_id": 9}],
                                                                "unlocked": ["recommended"], "state": {"momentum": 1}})
    body = client.post("/api/gamification/reconcile",
                       json={"session_id": "s1", "domain": "acme.com", "aeo_score": 72}).json()
    assert body["new_awards"] == [{"award_id": 9}]
    assert body["unlocked"] == ["recommended"]


def test_reconcile_is_best_effort_on_error(monkeypatch) -> None:
    from aeo.companion import rewards

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(rewards, "reconcile", boom)
    body = client.post("/api/gamification/reconcile", json={"session_id": "s1"}).json()
    assert body == {"new_awards": [], "unlocked": [], "state": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_gamification_api.py -v`
Expected: FAIL — routes 404.

- [ ] **Step 3: Add the endpoints**

In `src/aeo/api/app.py`, add a request model near the other request models:

```python
class GamifyReconcileRequest(BaseModel):
    session_id: str
    domain: str | None = None
    aeo_score: int | None = None
```

And the endpoints in the endpoints section:

```python
@app.get("/api/gamification")
def gamification_get(session_id: str, domain: str | None = None) -> dict[str, Any]:
    """The companion state + recent awards for a session. Best-effort: empty on any miss."""
    from ..storage.repos import gamification as gamification_repo

    try:
        state = gamification_repo.get_state(session_id)
        awards = gamification_repo.awards_for(session_id) if state else []
    except Exception:  # gamification must never break the app
        return {"state": None, "awards": []}
    return {"state": state, "awards": awards}


@app.post("/api/gamification/reconcile")
def gamification_reconcile(req: GamifyReconcileRequest) -> dict[str, Any]:
    """Recompute verified-win awards + score tiers for a session/domain. Idempotent +
    best-effort (a failure resolves to a no-op so the UI never breaks)."""
    from ..companion import rewards

    try:
        return rewards.reconcile(req.session_id, req.domain, aeo_score=req.aeo_score)
    except Exception:
        return {"new_awards": [], "unlocked": [], "state": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_gamification_api.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/api/app.py tests/unit/test_gamification_api.py
git commit -m "feat(gamification): /api/gamification get + reconcile endpoints"
```

---

### Task 5: Live-DB round-trip (integration)

**Files:**
- Test: `tests/integration/test_gamification_db.py`

- [ ] **Step 1: Apply migrations**

Run: `python -m aeo.cli migrate`
Expected: `0020`/`0021`/`0022` applied (or up-to-date).

- [ ] **Step 2: Write the integration test**

```python
# tests/integration/test_gamification_db.py
"""Live-DB round-trip for the gamification repo. Skips when no DB is reachable."""

from __future__ import annotations

import pytest

from aeo.storage.db import health_check
from aeo.storage.repos import gamification as gam

pytestmark = pytest.mark.skipif(not health_check(), reason="no reachable Postgres")


def test_state_award_and_idempotency() -> None:
    sid = "itest-session-gam"
    gam.upsert_state(sid, domain="acme.example", aeo_score=72, aeo_band="Recommended",
                     maturity_stage="recommended", momentum=1, verified_wins=1)
    state = gam.get_state(sid)
    assert state["aeo_score"] == 72 and state["maturity_stage"] == "recommended"

    first = gam.grant_award(sid, award_type="verified_win", source_table="recommendation_outcomes",
                            source_id=987654321, criterion="qa_blocks")
    again = gam.grant_award(sid, award_type="verified_win", source_table="recommendation_outcomes",
                            source_id=987654321, criterion="qa_blocks")
    assert first is not None
    assert again is None  # idempotent — same verdict row never double-grants
    assert any(a["source_id"] == 987654321 for a in gam.awards_for(sid))

    assert gam.unlock_achievement(sid, "recommended") is True
    assert gam.unlock_achievement(sid, "recommended") is False  # earn-once
```

- [ ] **Step 3: Run the integration test**

Run: `python -m pytest tests/integration/test_gamification_db.py -v`
Expected: 1 passed (or skipped if no DB).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_gamification_db.py
git commit -m "test(gamification): live-DB state + award idempotency round-trip"
```

---

### Task 6: Frontend — types, client, maturity helper, GamificationStrip

**Files:**
- Modify: `web/lib/types.ts`, `web/lib/api.ts`
- Create: `web/lib/gamify.ts`, `web/lib/gamify.test.ts`, `web/components/GamificationStrip.tsx`

- [ ] **Step 1: Write the failing maturity-helper test**

```typescript
// web/lib/gamify.test.ts
// Run: node --test lib/gamify.test.ts (or: npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import { MATURITY_LABEL, MATURITY_ORDER, maturityProgress } from "./gamify.ts";

test("maturityProgress is 0 at foundations and 1 at cited_leader", () => {
  assert.equal(maturityProgress("foundations"), 0);
  assert.equal(maturityProgress("cited_leader"), 1);
});

test("recommended sits halfway up the ladder", () => {
  assert.equal(maturityProgress("recommended"), MATURITY_ORDER.indexOf("recommended") / (MATURITY_ORDER.length - 1));
});

test("every stage has a human label", () => {
  for (const s of MATURITY_ORDER) assert.ok(MATURITY_LABEL[s]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web/`): `node --test lib/gamify.test.ts`
Expected: FAIL — cannot resolve `./gamify.ts`.

- [ ] **Step 3: Write the helper**

```typescript
// web/lib/gamify.ts
// Pure maturity-ladder helpers for the gamification UI — no I/O, unit-testable.

export const MATURITY_ORDER = ["foundations", "on_radar", "recommended", "authority", "cited_leader"] as const;
export type MaturityStage = (typeof MATURITY_ORDER)[number];

export const MATURITY_LABEL: Record<MaturityStage, string> = {
  foundations: "Foundations",
  on_radar: "On the radar",
  recommended: "Recommended",
  authority: "Authority",
  cited_leader: "Cited Leader",
};

/** 0–1 position of a stage on the ladder, for a progress bar. */
export function maturityProgress(stage: MaturityStage): number {
  const i = MATURITY_ORDER.indexOf(stage);
  return i < 0 ? 0 : i / (MATURITY_ORDER.length - 1);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `web/`): `node --test lib/gamify.test.ts`
Expected: 3 passed.

- [ ] **Step 5: Add the types**

Append to `web/lib/types.ts`:

```typescript
// ── gamification (Phase 3: honest, verified-outcome rewards) ─────────────────
export interface GamificationState {
  session_id: string;
  domain?: string | null;
  maturity_stage: string;
  aeo_score?: number | null;
  aeo_band?: string | null;
  momentum: number;
  verified_wins: number;
  citations_earned: number;
}

export interface GamificationAward {
  id: number;
  award_type: string;     // verified_win | citation | status_tier | maturity_up
  criterion?: string | null;
  score_delta?: number | null;
  created_at?: string | null;
}

export interface GamificationView {
  state: GamificationState | null;
  awards: GamificationAward[];
}
```

- [ ] **Step 6: Add the client methods**

In `web/lib/api.ts`, add `GamificationView` to the type imports, then add inside the `api` object:

```typescript
  // ── gamification (Phase 3) ────────────────────────────────────────────────
  /** Companion state + awards for this browser session. Best-effort: empty on any failure. */
  async getGamification(domain?: string): Promise<GamificationView> {
    if (typeof window === "undefined") return { state: null, awards: [] };
    const sid = getSessionId();
    const q = `session_id=${encodeURIComponent(sid)}${domain ? `&domain=${encodeURIComponent(domain)}` : ""}`;
    try {
      const res = await fetch(`${BASE}/api/gamification?${q}`, { headers: headers() });
      if (!res.ok) return { state: null, awards: [] };
      return (await res.json()) as GamificationView;
    } catch {
      return { state: null, awards: [] };
    }
  },
  /** Recompute verified-win awards + score tiers (idempotent). `aeoScore` is the canonical
   *  number from lib/score.ts so the backend never invents one. Best-effort. */
  reconcileGamification(domain: string, aeoScore?: number): Promise<GamificationView["state"] extends never ? never : unknown> {
    return postJson("/api/gamification/reconcile", { session_id: getSessionId(), domain, aeo_score: aeoScore ?? null });
  },
```

- [ ] **Step 7: Write the GamificationStrip component**

```tsx
// web/components/GamificationStrip.tsx
"use client";

import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { MATURITY_LABEL, type MaturityStage, maturityProgress } from "../lib/gamify";
import type { GamificationView } from "../lib/types";
import { CountUp, Tally } from "./motion/primitives";

/** A restrained status strip — the AEO band, verified-win count, and maturity. Reads the same
 *  verdict-backed state the engine computes; never invents progress. Renders nothing until
 *  there's real state, so a brand-new session sees no empty gamification chrome. */
export function GamificationStrip({ domain, aeoScore }: { domain?: string; aeoScore?: number }) {
  const [view, setView] = useState<GamificationView>({ state: null, awards: [] });

  useEffect(() => {
    let alive = true;
    async function load() {
      if (domain && typeof aeoScore === "number") {
        await api.reconcileGamification(domain, aeoScore).catch(() => {});
      }
      const v = await api.getGamification(domain);
      if (alive) setView(v);
    }
    void load();
    return () => {
      alive = false;
    };
  }, [domain, aeoScore]);

  const s = view.state;
  if (!s) return null;

  const stage = (s.maturity_stage as MaturityStage) ?? "foundations";
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-neutral-200 px-4 py-3 text-sm">
      <div>
        <span className="text-neutral-400">AEO</span>{" "}
        <CountUp to={s.aeo_score ?? 0} className="font-semibold" />{" "}
        <span className="text-neutral-500">{s.aeo_band ?? ""}</span>
      </div>
      <div>
        <span className="text-neutral-400">Verified wins</span>{" "}
        <Tally value={s.verified_wins} className="font-semibold" />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-neutral-400">{MATURITY_LABEL[stage]}</span>
        <span className="inline-block h-1.5 w-24 overflow-hidden rounded-full bg-neutral-100">
          <span
            className="block h-full rounded-full bg-neutral-800"
            style={{ width: `${Math.round(maturityProgress(stage) * 100)}%` }}
          />
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 8: Typecheck + lint + run pure-fn tests**

Run (from `web/`): `npm test` (the node:test suite incl. gamify.test.ts) — expect all pass.
Run (from `web/`): `npm run build` — expect a clean build. Fix any TS/lint error before continuing.

- [ ] **Step 9: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts web/lib/gamify.ts web/lib/gamify.test.ts web/components/GamificationStrip.tsx
git commit -m "feat(web): gamification client + maturity helper + GamificationStrip"
```

---

## Self-Review

**Spec coverage (design §3):** the honest reward core is built — rewards come ONLY from `recommendation_outcomes.implemented` (the Verified-live moat) and AEO-score milestones; awards are idempotent on the source verdict row (anti-inflation); momentum only moves on real wins; the canonical `aeoScore` stays the single headline number (no parallel currency). State is keyed on the existing `session_id` identity; the schema is additive (0020–0022) and reads existing verdict tables. The `GamificationStrip` reuses the existing `CountUp`/`Tally` motion vocabulary and `scoreBand` semantics, and renders nothing until there's real state (no empty gamification chrome for new sessions — the anti-childish principle).

**Placeholder scan:** none — all migrations, the repo, the reconciler, the endpoints, the integration test, the frontend client, the pure helper + its node:test, and the component are complete.

**Type/name consistency:** `gamification` repo functions (`get_state`/`upsert_state`/`grant_award`/`awards_for`/`unlock_achievement`), `rewards.reconcile(session_id, domain, *, aeo_score)` returning `{new_awards, unlocked, state}`, the `/api/gamification` endpoints, and the frontend `GamificationView`/`GamificationState`/`GamificationAward` types line up across backend and frontend. The maturity vocabulary (`foundations/on_radar/recommended/authority/cited_leader`) is identical in the migration CHECK, `rewards.maturity()`, and `web/lib/gamify.ts`. Migration numbers (0020–0022) follow 2A's 0019 with no collision.

**Deliberately deferred (Plan 3B, not gaps):** the conversational ATLAS narrator (`companion/narrator.py`), the LLM-phrased coach (`companion/coach.py`), the full `CompanionRail`, and the citations-earned axis (`citation_results`) + the `cited_leader` stage. The `aeo_score` is supplied by the caller (the frontend's canonical formula) rather than recomputed server-side — correct, since the score lives in `web/lib/score.ts`; if a server-side score is needed later, source it from `plan_states.score_snapshot`. The `reconcileGamification` return type is intentionally loose (`unknown`) since the UI re-reads via `getGamification`; tighten it in 3B if a typed reconcile response is needed.
```