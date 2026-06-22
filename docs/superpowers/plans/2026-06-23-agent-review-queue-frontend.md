# Agent Review Queue + SSE — Implementation Plan (Phase 2D)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the human approval gate a real UI. A reviewer sees staged agent runs, watches steps stream live (research → plan → build → critic), inspects each drafted page with its Critic verdict, and approves or rejects — all over the endpoints Plans 2A–2C already built.

**Architecture:** Two thin backend additions — a `GET /api/agent/runs?status=` list endpoint and a `GET /api/agent/run/{id}/stream` Server-Sent-Events endpoint that polls the durable `agent_runs`/`agent_steps` tables and streams deltas (works across the API↔worker process boundary; the Next proxy already streams response bodies straight through). The frontend adds typed client methods, a pure summary helper (node:test), and an `AgentReviewQueue` client component on a new `/agents` route, reusing the existing same-origin proxy + motion vocabulary.

**Tech Stack:** Backend: FastAPI + pytest (matches 2A–2C). Frontend: Next.js 15, React 19, framer-motion 12, TypeScript 5.7; tests via Node's built-in runner (`node --test "lib/**/*.test.ts"`); components verified via `next build` (typecheck + lint).

---

## Prerequisite

Plans 2A, 2B, 2C implemented and merged: `agent_runs`/`agent_steps`, `agent_runs` repo (`get`, `steps_for`, `set_status`, `list_by_status`), the controller (`research→plan→build→critic→staged`), and `/api/agent/run` start/status/approve/reject. The staged run's `result.tasks[]` carry `draft` (2B) and `critic` (2C); `agent_steps` carry `agent`/`status`/`tokens`/`cost_usd`.

## Scope

**In scope:** backend list + SSE endpoints; frontend agent types, client methods (incl. an SSE helper), a pure run-summary helper with a node:test, and the `AgentReviewQueue` component + `/agents` route. This is the complete human-review UI for the agent layer.

**Out of scope:** Plan 3 (gamification). No new tables (reuses 2A's schema); no auth changes (the same-origin proxy injects `X-API-Key`).

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/aeo/api/app.py` | Modify | `GET /api/agent/runs` (list by status) + `GET /api/agent/run/{id}/stream` (SSE). |
| `tests/unit/test_agent_api.py` | Modify | Cover the list + stream endpoints (monkeypatched repo). |
| `web/lib/types.ts` | Modify | `AgentRunSummary`, `AgentRunDetail`, `AgentStep`, `AgentTask`, `CriticVerdict`, `AgentStreamMessage`. |
| `web/lib/api.ts` | Modify | `startAgentRun`, `listAgentRuns`, `getAgentRun`, `approveAgentRun`, `rejectAgentRun`, `streamAgentRun`. |
| `web/lib/agentRun.ts` | Create | `summarizeRun(run)` pure helper (counts, cost, flag state). |
| `web/lib/agentRun.test.ts` | Create | node:test for `summarizeRun`. |
| `web/components/AgentReviewQueue.tsx` | Create | The review-queue client component (list + detail + approve/reject + live steps). |
| `web/app/agents/page.tsx` | Create | The `/agents` route hosting the queue. |

**Backend tests:** `python -m pytest`. **Frontend pure-fn tests:** from `web/`, `npm test` (`node --test "lib/**/*.test.ts"`). **Frontend build/typecheck:** from `web/`, `npm run build`.

---

### Task 1: Backend — list + SSE endpoints

**Files:**
- Modify: `src/aeo/api/app.py`
- Test: `tests/unit/test_agent_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_agent_api.py`:

```python
def test_list_agent_runs_returns_repo_rows(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "list_by_status", lambda status, limit=50: [{"id": "r1", "status": status}])
    body = client.get("/api/agent/runs?status=staged").json()
    assert body == {"runs": [{"id": "r1", "status": "staged"}]}


def test_stream_emits_steps_then_done_for_a_terminal_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: {"id": rid, "status": "staged",
                                                  "current_step": "review", "result": {"tasks": []}})
    monkeypatch.setattr(repo, "steps_for", lambda rid: [{"seq": 1, "agent": "planner", "status": "ok"}])

    with client.stream("GET", "/api/agent/run/r1/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert '"type": "step"' in body
    assert '"type": "done"' in body
    assert '"status": "staged"' in body


def test_stream_404s_for_unknown_run(monkeypatch) -> None:
    from aeo.storage.repos import agent_runs as repo

    monkeypatch.setattr(repo, "get", lambda rid: None)
    with client.stream("GET", "/api/agent/run/nope/stream") as r:
        body = "".join(r.iter_text())
    assert '"type": "error"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_agent_api.py -k "list_agent_runs or stream" -v`
Expected: FAIL — routes return 404 (not defined yet).

- [ ] **Step 3: Add the endpoints**

In `src/aeo/api/app.py`, add `import asyncio` and `from fastapi.responses import StreamingResponse` near the top imports (alongside the existing `from fastapi.responses import JSONResponse`). Then add to the agent-runs endpoint block (added in 2A):

```python
_AGENT_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


@app.get("/api/agent/runs")
def agent_runs_list(status: str = "staged", limit: int = 50) -> dict[str, Any]:
    """Agent runs in a given status (default 'staged' — the review queue)."""
    from ..storage.repos import agent_runs as agent_runs_repo

    return {"runs": agent_runs_repo.list_by_status(status, limit=max(1, min(limit, 200)))}


@app.get("/api/agent/run/{run_id}/stream")
async def agent_run_stream(run_id: str) -> StreamingResponse:
    """Stream a run's steps + status as Server-Sent Events. Polls the durable agent tables
    (so it works across the API↔worker process boundary) and closes once the run is terminal."""
    from ..storage.repos import agent_runs as agent_runs_repo

    async def gen():
        seen = 0
        for _ in range(600):  # ~10 min ceiling at 1s/poll
            row = await asyncio.to_thread(agent_runs_repo.get, run_id)
            if row is None:
                yield _sse({"type": "error", "detail": "unknown agent run"})
                return
            steps = await asyncio.to_thread(agent_runs_repo.steps_for, run_id)
            for step in steps[seen:]:
                yield _sse({"type": "step", "step": step})
            seen = len(steps)
            yield _sse({"type": "status", "status": row["status"], "current_step": row.get("current_step")})
            if row["status"] in _AGENT_TERMINAL:
                yield _sse({"type": "done", "status": row["status"], "result": row.get("result")})
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_agent_api.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/api/app.py tests/unit/test_agent_api.py
git commit -m "feat(agents): /api/agent/runs list + SSE stream endpoints"
```

---

### Task 2: Frontend — types + client methods

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts`

- [ ] **Step 1: Add the types**

Append to `web/lib/types.ts`:

```typescript
// ── agent runs (Phase 2: assistive copilot + human review) ───────────────────
export interface AgentStep {
  seq: number;
  agent: string;            // planner | research | builder | critic
  tool?: string | null;
  status: string;           // ok | failed | skipped
  model?: string | null;
  tokens?: number | null;
  cost_usd?: number | null;
  detail?: Record<string, unknown> | null;
}

export interface CriticVerdict {
  passed: boolean;
  independent_passed: boolean;
  claims_flagged: boolean;
  claims: string[];
  needs_review: boolean;
}

export interface AgentTask {
  id: string;
  title: string;
  slug?: string;
  status: string;           // proposed | drafted | reviewed | flagged
  draft?: { body_markdown?: string; draft_quality?: string; word_count?: number } | null;
  critic?: CriticVerdict | null;
}

export interface AgentRunSummary {
  id: string;
  status: string;           // queued | planning | staged | approved | rejected | failed | cancelled
  domain?: string | null;
  current_step?: string | null;
  updated_at?: string | null;
}

export interface AgentRunDetail extends AgentRunSummary {
  result?: { domain?: string; topic?: string; headline?: string; tasks?: AgentTask[] } | null;
  steps?: AgentStep[];
}

export type AgentStreamMessage =
  | { type: "step"; step: AgentStep }
  | { type: "status"; status: string; current_step?: string | null }
  | { type: "done"; status: string; result?: AgentRunDetail["result"] }
  | { type: "error"; detail: string };
```

- [ ] **Step 2: Add the client methods**

In `web/lib/api.ts`, add these to the imported type list at the top:

```typescript
  AgentRunDetail,
  AgentRunSummary,
  AgentStreamMessage,
```

Then add these methods inside the `export const api = { ... }` object (e.g. after `recheckStatus`):

```typescript
  // ── agent runs (Phase 2) ──────────────────────────────────────────────────
  startAgentRun(req: BriefRequest): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>("/api/agent/run", req);
  },
  async listAgentRuns(status = "staged"): Promise<{ runs: AgentRunSummary[] }> {
    const res = await fetch(`${BASE}/api/agent/runs?status=${encodeURIComponent(status)}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as { runs: AgentRunSummary[] };
  },
  async getAgentRun(runId: string): Promise<AgentRunDetail> {
    const res = await fetch(`${BASE}/api/agent/run/${encodeURIComponent(runId)}`, { headers: headers() });
    if (!res.ok) throw new Error(`API ${res.status} ${res.statusText}`);
    return (await res.json()) as AgentRunDetail;
  },
  approveAgentRun(runId: string): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>(`/api/agent/run/${encodeURIComponent(runId)}/approve`, {});
  },
  rejectAgentRun(runId: string): Promise<{ run_id: string; status: string }> {
    return postJson<{ run_id: string; status: string }>(`/api/agent/run/${encodeURIComponent(runId)}/reject`, {});
  },
  /** Live run updates via SSE (browser only). The same-origin proxy injects auth; EventSource
   *  can't set headers. Returns the EventSource so the caller can close() it on unmount. */
  streamAgentRun(runId: string, onMessage: (msg: AgentStreamMessage) => void): EventSource | null {
    if (typeof window === "undefined" || typeof EventSource === "undefined") return null;
    const es = new EventSource(`${BASE}/api/agent/run/${encodeURIComponent(runId)}/stream`);
    es.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data) as AgentStreamMessage);
      } catch {
        /* ignore malformed frame */
      }
    };
    es.onerror = () => es.close();
    return es;
  },
```

- [ ] **Step 3: Typecheck**

Run (from `web/`): `npm run build`
Expected: build succeeds (types compile; no lint errors). If `next build` needs the backend, it won't — it only typechecks/bundles. Fix any TS error surfaced before continuing.

- [ ] **Step 4: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts
git commit -m "feat(web): agent-run types + client methods (incl. SSE)"
```

---

### Task 3: Frontend — pure run-summary helper + node:test

**Files:**
- Create: `web/lib/agentRun.ts`
- Test: `web/lib/agentRun.test.ts`

- [ ] **Step 1: Write the failing test** (mirrors `web/lib/goals.test.ts`: node:test, explicit `.ts` imports)

```typescript
// web/lib/agentRun.test.ts
// Unit tests for summarizeRun. Run: node --test lib/agentRun.test.ts (or: npm test, from web/)

import test from "node:test";
import assert from "node:assert/strict";

import { summarizeRun } from "./agentRun.ts";
import type { AgentRunDetail } from "./types.ts";

function run(over: Partial<AgentRunDetail>): AgentRunDetail {
  return {
    id: "r1",
    status: "staged",
    result: {
      tasks: [
        { id: "page:/a", title: "A", status: "reviewed", draft: { word_count: 100 }, critic: { passed: true, independent_passed: true, claims_flagged: false, claims: [], needs_review: false } },
        { id: "page:/b", title: "B", status: "flagged", draft: { word_count: 80 }, critic: { passed: false, independent_passed: false, claims_flagged: true, claims: ["#1"], needs_review: true } },
      ],
    },
    steps: [
      { seq: 1, agent: "planner", status: "ok" },
      { seq: 2, agent: "builder", status: "ok", cost_usd: 0.01 },
      { seq: 3, agent: "critic", status: "ok", cost_usd: 0.02 },
    ],
    ...over,
  };
}

test("counts drafted, flagged, and totals cost", () => {
  const s = summarizeRun(run({}));
  assert.equal(s.taskCount, 2);
  assert.equal(s.draftedCount, 2);
  assert.equal(s.flaggedCount, 1);
  assert.equal(s.costUsd.toFixed(2), "0.03");
});

test("isStaged reflects status", () => {
  assert.equal(summarizeRun(run({ status: "staged" })).isStaged, true);
  assert.equal(summarizeRun(run({ status: "approved" })).isStaged, false);
});

test("empty/absent result is safe", () => {
  const s = summarizeRun({ id: "x", status: "queued" });
  assert.equal(s.taskCount, 0);
  assert.equal(s.flaggedCount, 0);
  assert.equal(s.costUsd, 0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web/`): `node --test lib/agentRun.test.ts`
Expected: FAIL — cannot resolve `./agentRun.ts`.

- [ ] **Step 3: Write the helper**

```typescript
// web/lib/agentRun.ts
// Pure helpers for rendering an agent run in the review queue — no I/O, unit-testable.

import type { AgentRunDetail } from "./types";

export interface RunSummary {
  isStaged: boolean;
  taskCount: number;
  draftedCount: number;
  flaggedCount: number;
  costUsd: number;
}

export function summarizeRun(run: AgentRunDetail): RunSummary {
  const tasks = run.result?.tasks ?? [];
  const steps = run.steps ?? [];
  return {
    isStaged: run.status === "staged",
    taskCount: tasks.length,
    draftedCount: tasks.filter((t) => !!t.draft).length,
    flaggedCount: tasks.filter((t) => t.critic?.needs_review).length,
    costUsd: steps.reduce((sum, s) => sum + (s.cost_usd ?? 0), 0),
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `web/`): `node --test lib/agentRun.test.ts`
Expected: 3 passed (`# pass 3`).

- [ ] **Step 5: Commit**

```bash
git add web/lib/agentRun.ts web/lib/agentRun.test.ts
git commit -m "feat(web): summarizeRun helper for the review queue"
```

---

### Task 4: Frontend — AgentReviewQueue component + /agents route

**Files:**
- Create: `web/components/AgentReviewQueue.tsx`
- Create: `web/app/agents/page.tsx`

- [ ] **Step 1: Write the component**

```tsx
// web/components/AgentReviewQueue.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { summarizeRun } from "../lib/agentRun";
import type { AgentRunDetail, AgentRunSummary, AgentStreamMessage } from "../lib/types";

const TERMINAL = new Set(["staged", "approved", "rejected", "failed", "cancelled"]);

export function AgentReviewQueue() {
  const [runs, setRuns] = useState<AgentRunSummary[]>([]);
  const [selected, setSelected] = useState<AgentRunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const refreshList = useCallback(async () => {
    try {
      const { runs } = await api.listAgentRuns("staged");
      setRuns(runs);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const open = useCallback((id: string) => {
    esRef.current?.close();
    setSelected(null);
    void api.getAgentRun(id).then(setSelected).catch((e) => setError((e as Error).message));
    // Live updates while the run is still working; closes itself on the terminal frame.
    esRef.current = api.streamAgentRun(id, (msg: AgentStreamMessage) => {
      if (msg.type === "done" || msg.type === "status") {
        if (msg.type === "done" || (msg.type === "status" && TERMINAL.has(msg.status))) {
          void api.getAgentRun(id).then(setSelected).catch(() => {});
        }
      }
    });
  }, []);

  useEffect(() => () => esRef.current?.close(), []);

  const decide = useCallback(
    async (id: string, decision: "approve" | "reject") => {
      setBusy(true);
      setError(null);
      try {
        if (decision === "approve") await api.approveAgentRun(id);
        else await api.rejectAgentRun(id);
        setSelected(null);
        await refreshList();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [refreshList],
  );

  const summary = selected ? summarizeRun(selected) : null;

  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[18rem_1fr]">
      <aside className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">Review queue</h2>
          <button onClick={() => void refreshList()} className="text-xs text-neutral-400 hover:text-neutral-700">
            Refresh
          </button>
        </div>
        {runs.length === 0 && <p className="text-sm text-neutral-400">No staged runs.</p>}
        <ul className="space-y-1">
          {runs.map((r) => (
            <li key={r.id}>
              <button
                onClick={() => open(r.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${
                  selected?.id === r.id ? "border-neutral-900 bg-neutral-50" : "border-neutral-200 hover:bg-neutral-50"
                }`}
              >
                <span className="font-medium">{r.domain ?? r.id}</span>
                <span className="ml-2 text-xs text-neutral-400">{r.status}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section>
        {error && <p className="mb-3 rounded bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
        {!selected && <p className="text-sm text-neutral-400">Select a run to review.</p>}
        {selected && summary && (
          <div className="space-y-4">
            <header className="flex items-center justify-between">
              <div>
                <h1 className="text-lg font-semibold">{selected.result?.headline ?? selected.domain ?? selected.id}</h1>
                <p className="text-xs text-neutral-500">
                  {summary.draftedCount} drafted · {summary.flaggedCount} flagged · ${summary.costUsd.toFixed(3)} ·{" "}
                  {selected.status}
                  {selected.current_step ? ` (${selected.current_step})` : ""}
                </p>
              </div>
              {summary.isStaged && (
                <div className="flex gap-2">
                  <button
                    disabled={busy}
                    onClick={() => void decide(selected.id, "reject")}
                    className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50 disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => void decide(selected.id, "approve")}
                    className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm text-white hover:bg-neutral-700 disabled:opacity-50"
                  >
                    Approve
                  </button>
                </div>
              )}
            </header>

            <ol className="flex flex-wrap gap-2 text-xs">
              {(selected.steps ?? []).map((s) => (
                <li key={s.seq} className="rounded-full border border-neutral-200 px-2 py-0.5">
                  {s.agent} · {s.status}
                  {s.cost_usd ? ` · $${s.cost_usd.toFixed(3)}` : ""}
                </li>
              ))}
            </ol>

            <ul className="space-y-3">
              {(selected.result?.tasks ?? [])
                .filter((t) => t.draft)
                .map((t) => (
                  <li key={t.id} className="rounded-xl border border-neutral-200 p-4">
                    <div className="flex items-center justify-between">
                      <h3 className="font-medium">{t.title}</h3>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs ${
                          t.critic?.needs_review ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-800"
                        }`}
                      >
                        {t.critic?.needs_review ? "Needs review" : "Looks clean"}
                      </span>
                    </div>
                    {t.critic?.claims_flagged && (
                      <p className="mt-1 text-xs text-amber-700">Claims to verify: {t.critic.claims.join(", ")}</p>
                    )}
                    <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-neutral-50 p-3 text-xs text-neutral-700">
                      {t.draft?.body_markdown ?? ""}
                    </pre>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Write the route**

```tsx
// web/app/agents/page.tsx
import { AgentReviewQueue } from "../../components/AgentReviewQueue";
import { MotionProvider } from "../../components/motion/primitives";

export const metadata = { title: "Agent Review Queue · AEO Studio" };

export default function AgentsPage() {
  return (
    <MotionProvider>
      <main className="mx-auto max-w-5xl px-6 py-10">
        <AgentReviewQueue />
      </main>
    </MotionProvider>
  );
}
```

- [ ] **Step 3: Typecheck + lint the whole web app**

Run (from `web/`): `npm run build`
Expected: build succeeds; the `/agents` route is emitted. Fix any TS/lint error before continuing.

- [ ] **Step 4: Manual end-to-end** (optional but recommended; needs the backend + a staged run from 2A–2C)

Start the API (`python -m aeo.cli serve`) and the web app (`cd web && npm run dev`). Create a staged run (`python -m aeo.cli agent Acme --domain acme.com --topic ctem`, then `python -m aeo.cli worker`). Open http://localhost:3000/agents — the run appears in the queue; clicking it shows steps + drafts + Critic badges; Approve/Reject flips it and removes it from the queue.

- [ ] **Step 5: Commit**

```bash
git add web/components/AgentReviewQueue.tsx web/app/agents/page.tsx
git commit -m "feat(web): AgentReviewQueue component + /agents route"
```

---

## Self-Review

**Spec coverage:** the human approval gate now has a UI (design §2.6 HITL); steps stream live over SSE (design §2.8 observability / the "poll-only is a bottleneck" gap); each draft shows its Critic verdict (2C) and the run shows per-step cost (2B). Nothing publishes from the UI — only Approve/Reject, which call the gated 2A endpoints.

**Placeholder scan:** none — backend code, the pure helper, its node:test, the full component, and the route are all complete.

**Type/name consistency:** `AgentRunSummary`/`AgentRunDetail`/`AgentStep`/`AgentTask`/`CriticVerdict`/`AgentStreamMessage` are defined once in `types.ts` and consumed by `api.ts`, `agentRun.ts`, and the component identically. The SSE message shapes emitted by the backend (`{type:"step"|"status"|"done"|"error", ...}`) match the `AgentStreamMessage` union. The component reads `result.tasks[].draft`/`.critic` exactly as 2B/2C write them.

**Notes / deferred:** the SSE endpoint polls the DB at 1s (simple + cross-process correct); a future optimization could use Postgres LISTEN/NOTIFY for push, but that's not needed at current scale. The component is functional and typechecks; visual polish (matching the wizard's design system) is left to taste and is non-blocking. There's no `/agents` link in the main nav yet — add one in `web/app/page.tsx`/layout when the queue graduates from internal-tool to product surface.
```