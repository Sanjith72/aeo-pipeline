# Critic Pipeline — Implementation Plan (Phase 2C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a model-isolated quality + safety gate between the Builder and the human approval queue. Every staged draft is checked by deterministic independent signals, an adversarial "refute this" auditor, and a claim/compliance auditor — each draft is annotated with a verdict and flagged for human attention when it fails or makes unverifiable claims. The Critic never publishes and never auto-rejects; it sharpens the human gate.

**Architecture:** Extends `AgentRunController` from `research → plan → build → staged` to `research → plan → build → critic → staged`. The Critic wraps three existing/lightweight seams over each `task['draft']`: `validation.draft_check.validate_page_draft` (non-circular Independent Validator + citation-signal check, deterministic by default), `validation.adversarial.adversarial_audit` (model-isolated refute + citation-hallucination, deterministic floor), and a new `claim_audit` (frontier claim extractor with a regex floor — the merged Safety/Compliance agent). Verdicts are attached under `task['critic']` and surfaced through the existing `GET /api/agent/run/{id}` (no new endpoint). Cost is recorded onto the `agent_steps` row via the `InstrumentedLLM` from 2B.

**Tech Stack:** Python 3.11, pytest. Builds on Plans 2A + 2B. Reuses `validate_page_draft`, `adversarial_audit`, `InstrumentedLLM`, `get_planning_client` unchanged.

---

## Prerequisite

Plans 2A and 2B are implemented and merged: `agent_runs`/`agent_steps`, the `AgentRunController` (`research → plan → build → staged`), `InstrumentedLLM`, `get_planning_client`, and the `/api/agent/run` endpoints all exist and pass.

## Scope

**In scope:** the Critic agent (`agents/critic.py`: `claim_audit` + `review_drafts`), the `critic_enabled` flag, and the controller rewire to run the critic step before staging — with per-draft verdicts and cost recorded.

**Out of scope — Plan 2D (frontend):** `web/components/AgentReviewQueue.tsx`, the staged-run list/detail UI, and per-step SSE streaming. These are a separate subsystem (React/Next + transport) and deliberately follow the Critic: the review queue UI **renders** the Critic verdicts this plan produces. Its data source already exists — `GET /api/agent/run/{id}` returns the full run including `result.tasks[].critic` and the per-step trace — so no backend API work is needed here for 2D to consume. (This split follows the same scope discipline as 2A/2B: one coherent, testable unit per plan.)

**No new migration, no new endpoint:** verdicts ride the existing `agent_runs.result` JSONB (`tasks[].critic`); critic cost rides the existing `agent_steps` columns; sub-phase is the free-text `current_step='critic'`.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/aeo/settings.py` | Modify | `AgentsCfg.critic_enabled`. |
| `src/aeo/agents/critic.py` | Create | `claim_audit` (frontier + regex floor) + `review_drafts` (independent + adversarial + claim → per-draft verdict). |
| `src/aeo/agents/runtime.py` | Modify | Controller: add the `critic` step before `staged`; record verdict counts + cost. |
| `src/aeo/agents/__init__.py` | Modify | Re-export `review_drafts`. |
| `tests/unit/test_agent_critic.py` | Create | `claim_audit` floor + `review_drafts` annotation/flagging (offline). |
| `tests/unit/test_agent_runtime.py` | Modify | Expect the 4-step `research→plan→build→critic` flow. |

**Run tests** with `python -m pytest` from the repo root.

---

### Task 1: Settings — critic_enabled

**Files:**
- Modify: `src/aeo/settings.py`
- Test: `tests/unit/test_agents_settings.py` (extend)

- [ ] **Step 1: Add the failing assertion**

Append to `tests/unit/test_agents_settings.py`:

```python
def test_agents_cfg_has_critic_flag() -> None:
    from aeo.settings import AgentsCfg

    assert AgentsCfg().critic_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agents_settings.py::test_agents_cfg_has_critic_flag -v`
Expected: FAIL — `AttributeError: 'AgentsCfg' object has no attribute 'critic_enabled'`.

- [ ] **Step 3: Add the flag**

In `class AgentsCfg(BaseModel):`, alongside the other agent-step flags:

```python
    critic_enabled: bool = True     # gate staged drafts (independent + adversarial + claim audit) before human review
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agents_settings.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/settings.py tests/unit/test_agents_settings.py
git commit -m "feat(agents): AgentsCfg.critic_enabled flag"
```

---

### Task 2: Critic agent — claim_audit + review_drafts

**Files:**
- Create: `src/aeo/agents/critic.py`
- Test: `tests/unit/test_agent_critic.py`

- [ ] **Step 1: Write the failing test** (offline — conftest disables the LLM, so every check uses its deterministic floor)

```python
# tests/unit/test_agent_critic.py
"""Critic agent: per-draft independent + adversarial + claim verdicts (deterministic floors)."""

from __future__ import annotations

from aeo.agents.critic import claim_audit, review_drafts


def _draft_payload(body: str = "# Page\n\nA clean, liftable answer in one short sentence.\n") -> dict:
    return {
        "body_markdown": body,
        "jsonld": [{"@context": "https://schema.org", "@type": "WebPage", "name": "Page"}],
        "h1": "Page", "meta_description": "x", "sections": [], "faq": [],
    }


def test_claim_audit_flags_stats_and_superlatives_deterministically() -> None:
    res = claim_audit("We are the #1 leading platform with a 99% uptime guarantee.", llm=None)
    assert res["flagged"] is True
    assert res["source"] == "deterministic"
    assert res["claims"], "expected at least one flagged claim phrase"


def test_claim_audit_passes_clean_prose() -> None:
    res = claim_audit("This page explains how the process works and who it helps.", llm=None)
    assert res["flagged"] is False


def test_review_annotates_every_draft() -> None:
    graph = {"tasks": [{"id": "page:/x", "slug": "/x", "draft": _draft_payload()}]}
    out = review_drafts(graph, llm=None, origin="https://acme.com")
    verdict = out["tasks"][0]["critic"]
    assert set(verdict) >= {"passed", "independent_passed", "adversarial", "claims_flagged", "needs_review"}
    assert isinstance(verdict["needs_review"], bool)


def test_review_fails_a_draft_with_a_hallucinated_citation() -> None:
    # 'http://no-dot-host/page' has a netloc with no dot → structurally invalid → hallucinated,
    # with no network needed (the deterministic citation-signal floor catches it).
    graph = {"tasks": [{"id": "x", "slug": "/x", "draft": _draft_payload("Source: http://no-dot-host/page")}]}
    out = review_drafts(graph, llm=None, origin=None)
    verdict = out["tasks"][0]["critic"]
    assert verdict["adversarial"]["hallucinated"] >= 1
    assert verdict["passed"] is False
    assert verdict["needs_review"] is True
    assert out["tasks"][0]["status"] == "flagged"


def test_review_skips_tasks_without_a_draft() -> None:
    graph = {"tasks": [{"id": "x", "slug": "/x"}]}
    out = review_drafts(graph, llm=None)
    assert "critic" not in out["tasks"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_critic.py -v`
Expected: FAIL — `ModuleNotFoundError: aeo.agents.critic`.

- [ ] **Step 3: Write the Critic agent**

```python
# src/aeo/agents/critic.py
"""Critic agent — a model-isolated quality + safety gate over staged drafts.

For each drafted page task, three checks run, each deterministic-first (no network unless a
client is supplied + enabled):

  1. INDEPENDENT (validation.draft_check.validate_page_draft): the non-circular Independent
     Validator (lead-answer liftable, H1-is-a-question, valid JSON-LD) + citation-signal check.
  2. ADVERSARIAL (validation.adversarial.adversarial_audit): a distinct 'refute this' persona
     (isolation is in the prompt, not the vendor) + deterministic citation-hallucination check.
  3. CLAIM/COMPLIANCE (claim_audit): extract specific factual/statistical claims that a
     publisher must verify before shipping (the merged Safety/Compliance auditor).

The Critic ANNOTATES each task with a verdict under ``task['critic']`` and flags it for human
attention; it NEVER publishes and NEVER auto-rejects. The human approval gate (2A
/api/agent/run/{id}/approve|reject) remains the sole authority. A draft 'passes' only when the
independent checks pass AND the adversarial auditor did not refute it.
"""

from __future__ import annotations

import re
from typing import Any

from ..validation.adversarial import adversarial_audit
from ..validation.draft_check import validate_page_draft

# Deterministic floor for the claim auditor: stat/superlative/guarantee phrasing a publisher
# should verify. Used when no LLM is available (or as the cheap default).
_CLAIM_RE = re.compile(
    r"\b(\d+(?:\.\d+)?\s?%|no\.?\s?1\b|#\s?1\b|\bguarantee(?:d|s)?\b|\bcertified\b|"
    r"\bleading\b|\bbest[- ]in[- ]class\b|\b(?:fastest|cheapest|largest|#1)\b)",
    re.I,
)
_MAX_CLAIM_TEXT = 4000

_CLAIM_SYSTEM = (
    "You are a compliance reviewer for marketing copy. Extract every SPECIFIC factual or "
    "statistical claim in the text that a publisher would need to verify before publishing — "
    "numbers, percentages, superlatives (e.g. 'the leading', '#1'), named certifications, and "
    "guarantees. Do NOT invent claims that are not in the text. Reply with JSON only: "
    '{"claims": ["...", "..."]}.'
)


def claim_audit(text: str, *, llm: Any = None) -> dict[str, Any]:
    """Flag verifiable factual/statistical claims in ``text``. Frontier extraction when a model
    is enabled, a regex floor otherwise. Returns ``{flagged, claims, source}``. Never raises."""
    snippet = (text or "")[:_MAX_CLAIM_TEXT]
    if llm is not None and getattr(llm, "enabled", False):
        try:
            data = llm.generate_json(f"Text to review:\n{snippet}", _CLAIM_SYSTEM)
        except Exception:  # never let the auditor break a run
            data = None
        if isinstance(data, dict):
            claims = [str(c).strip() for c in (data.get("claims") or []) if str(c).strip()][:20]
            return {"flagged": bool(claims), "claims": claims, "source": "llm"}
    hits = sorted({m.group(0).strip() for m in _CLAIM_RE.finditer(snippet)})
    return {"flagged": bool(hits), "claims": hits, "source": "deterministic"}


def review_drafts(
    graph: dict[str, Any],
    *,
    llm: Any = None,
    origin: str | None = None,
    verify_citations: bool = False,
    adversarial_max_attempts: int = 3,
) -> dict[str, Any]:
    """Annotate every drafted task with a Critic verdict. Mutates the graph in place; returns it.
    ``llm`` may be an InstrumentedLLM so the caller can aggregate cost afterward."""
    for task in graph.get("tasks", []):
        draft = task.get("draft")
        if not draft:
            continue
        slug = task.get("slug") or ""
        url = f"{origin}{slug}" if origin else None
        body = str(draft.get("body_markdown", ""))

        independent = validate_page_draft(draft, url=url, verify_reachability=verify_citations)
        adversarial = adversarial_audit(
            body, llm=llm, verify_reachability=verify_citations, max_attempts=adversarial_max_attempts
        )
        claims = claim_audit(body, llm=llm)

        passed = bool(independent["passed"]) and adversarial.passed
        needs_review = (not passed) or claims["flagged"]
        task["critic"] = {
            "passed": passed,
            "independent_passed": bool(independent["passed"]),
            "independent": independent,
            "adversarial": adversarial.to_detail(),
            "claims_flagged": claims["flagged"],
            "claims": claims["claims"],
            "needs_review": needs_review,
        }
        task["status"] = "reviewed" if not needs_review else "flagged"
    return graph
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_critic.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/agents/critic.py tests/unit/test_agent_critic.py
git commit -m "feat(agents): Critic agent (independent + adversarial + claim audit)"
```

---

### Task 3: Wire the critic step into the controller

**Files:**
- Modify: `src/aeo/agents/runtime.py`
- Modify: `src/aeo/agents/__init__.py`
- Test: `tests/unit/test_agent_runtime.py` (extend for the 4-step flow)

- [ ] **Step 1: Extend the controller test for the critic step**

In `tests/unit/test_agent_runtime.py`, update the `_ctrl` helper to accept a `critic` fake and add the flow assertion. Replace the `_ctrl` helper with:

```python
def _ctrl(repo, *, research=None, planner=None, builder=None, critic=None, cfg=None):
    from aeo.agents.runtime import AgentRunController

    return AgentRunController(
        research=research or (lambda brief, **kw: {"competitors": []}),
        planner=planner or (lambda brief: {"topic": "ctem", "tasks": [{"id": "t", "kind": "content"}]}),
        builder=builder or (lambda graph, **kw: graph),
        critic=critic or (lambda graph, **kw: graph),
        repo=repo,
        llm_provider=lambda: None,
        cfg=cfg or AgentsCfg(),
    )
```

Replace `test_full_flow_records_research_plan_build_in_order` with:

```python
def test_full_flow_records_research_plan_build_critic_in_order() -> None:
    repo = FakeRepo(_row())
    research = lambda brief, **kw: {"competitors": [{"name": "R7", "domain": "rapid7.com"}]}
    planner = lambda brief: {"topic": "ctem", "tasks": [{"id": "page:/x", "kind": "content"}]}
    builder = lambda graph, **kw: {**graph, "built": True}
    critic = lambda graph, **kw: {**graph, "critiqued": True}

    out = _ctrl(repo, research=research, planner=planner, builder=builder, critic=critic).run("run1")

    assert out["status"] == "staged"
    assert out["result"]["built"] is True
    assert out["result"]["critiqued"] is True
    assert [(s["seq"], s["agent"]) for s in repo.steps] == [
        (1, "research"), (2, "planner"), (3, "builder"), (4, "critic")
    ]
```

Update `test_flags_off_runs_planner_only` to also disable the critic:

```python
def test_flags_off_runs_planner_only() -> None:
    repo = FakeRepo(_row())
    cfg = AgentsCfg(research_enabled=False, build_enabled=False, critic_enabled=False)
    _ctrl(repo, cfg=cfg).run("run1")
    assert [(s["seq"], s["agent"]) for s in repo.steps] == [(1, "planner")]
```

And update the planner-failure test's cfg to disable the critic too:

```python
def test_planner_failure_marks_failed_and_reraises() -> None:
    repo = FakeRepo(_row())

    def boom(brief):
        raise RuntimeError("planner exploded")

    cfg = AgentsCfg(research_enabled=False, build_enabled=False, critic_enabled=False)
    with pytest.raises(RuntimeError, match="planner exploded"):
        _ctrl(repo, planner=boom, cfg=cfg).run("run1")
    assert repo.runs["run1"]["status"] == "failed"
    assert repo.steps[0]["status"] == "failed"
    assert repo.steps[0]["error_class"] == "RuntimeError"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_runtime.py -v`
Expected: FAIL — `AgentRunController.__init__` got an unexpected keyword `critic`.

- [ ] **Step 3: Replace the `AgentRunController` with the 4-step version**

In `src/aeo/agents/runtime.py`, add the import alongside the other agent imports:

```python
from .critic import review_drafts
```

Then replace the `AgentRunController` class with this (the `__init__` gains `critic`; `run` computes `origin` once and adds the critic step before staging):

```python
class AgentRunController:
    def __init__(
        self,
        *,
        research=research_competitors,
        planner=plan_tasks,
        builder=build_drafts,
        critic=review_drafts,
        repo=agent_runs_repo,
        brief_builder=brief_from_dict,
        llm_provider=_planning_client,
        cfg=None,
    ) -> None:
        self._research = research
        self._planner = planner
        self._builder = builder
        self._critic = critic
        self._repo = repo
        self._brief = brief_builder
        self._llm_provider = llm_provider
        self._cfg = cfg

    def run(self, run_id: str) -> dict[str, Any]:
        """research → plan → build → critic → staged. Each step has a deterministic floor;
        idempotent (a terminal run is a no-op under at-least-once delivery)."""
        from ..settings import get_settings

        row = self._repo.get(run_id)
        if row is None:
            raise ValueError(f"unknown agent run: {run_id!r}")
        if row["status"] in _TERMINAL:
            return row

        cfg = self._cfg or get_settings().agents
        vcfg = get_settings().validation
        brief_dict = dict(row.get("brief") or {})
        seq = 0

        # ── research (best-effort; deterministic-first) ──
        if cfg.research_enabled:
            self._repo.set_status(run_id, "planning", current_step="research")
            try:
                res = self._research(brief_dict, llm=self._llm_provider())
            except Exception:
                res = {"competitors": []}
            competitors = res.get("competitors") or []
            if competitors:
                brief_dict = {**brief_dict, "competitors": [c["domain"] or c["name"] for c in competitors]}
            seq += 1
            self._repo.append_step(
                run_id, seq=seq, agent="research", tool="discover_competitors", status="ok",
                detail={"verified": len(competitors)},
            )

        # ── plan (deterministic) ──
        self._repo.set_status(run_id, "planning", current_step="plan")
        brief = self._brief(brief_dict)
        origin = f"https://{brief.key()}" if brief.domain else None
        seq += 1
        try:
            graph = self._planner(brief)
        except Exception as exc:
            self._repo.append_step(
                run_id, seq=seq, agent="planner", tool="plan_from_brief", status="failed",
                error_class=type(exc).__name__, detail={"error": str(exc)},
            )
            self._repo.set_status(run_id, "failed", error=str(exc))
            raise
        self._repo.append_step(
            run_id, seq=seq, agent="planner", tool="plan_from_brief", status="ok",
            detail={"task_count": len(graph.get("tasks", []))},
        )

        # ── build (deterministic floor; cost recorded) ──
        if cfg.build_enabled:
            self._repo.set_status(run_id, "planning", current_step="build")
            client = self._llm_provider()
            inst = InstrumentedLLM(client) if client is not None else None
            seq += 1
            try:
                graph = self._builder(graph, llm=inst, origin=origin, limit=cfg.draft_limit)
            except Exception as exc:
                self._repo.append_step(
                    run_id, seq=seq, agent="builder", tool="draft_missing_page", status="failed",
                    error_class=type(exc).__name__, detail={"error": str(exc)},
                )
                self._repo.set_status(run_id, "failed", error=str(exc))
                raise
            drafted = sum(1 for t in graph.get("tasks", []) if t.get("draft"))
            totals = inst.totals() if inst else {"tokens": None, "cost_usd": None, "llm_calls": 0}
            self._repo.append_step(
                run_id, seq=seq, agent="builder", tool="draft_missing_page", status="ok",
                model=(inst.model if inst else None),
                tokens=totals["tokens"], cost_usd=totals["cost_usd"],
                detail={"drafts": drafted, "llm_calls": totals["llm_calls"]},
            )

        # ── critic (model-isolated gate; deterministic floor; cost recorded) ──
        if cfg.critic_enabled:
            self._repo.set_status(run_id, "planning", current_step="critic")
            client = self._llm_provider()
            inst = InstrumentedLLM(client) if client is not None else None
            seq += 1
            try:
                graph = self._critic(
                    graph, llm=inst, origin=origin,
                    verify_citations=vcfg.verify_citations,
                    adversarial_max_attempts=vcfg.adversarial_max_attempts,
                )
            except Exception as exc:
                self._repo.append_step(
                    run_id, seq=seq, agent="critic", tool="adversarial_audit", status="failed",
                    error_class=type(exc).__name__, detail={"error": str(exc)},
                )
                self._repo.set_status(run_id, "failed", error=str(exc))
                raise
            reviewed = sum(1 for t in graph.get("tasks", []) if t.get("critic"))
            flagged = sum(1 for t in graph.get("tasks", []) if t.get("critic", {}).get("needs_review"))
            totals = inst.totals() if inst else {"tokens": None, "cost_usd": None, "llm_calls": 0}
            self._repo.append_step(
                run_id, seq=seq, agent="critic", tool="adversarial_audit", status="ok",
                model=(inst.model if inst else None),
                tokens=totals["tokens"], cost_usd=totals["cost_usd"],
                detail={"reviewed": reviewed, "flagged": flagged, "llm_calls": totals["llm_calls"]},
            )

        self._repo.set_status(run_id, "staged", current_step="review", result=graph)
        return self._repo.get(run_id)
```

- [ ] **Step 4: Re-export the critic**

In `src/aeo/agents/__init__.py`:

```python
from .builder import build_drafts
from .critic import review_drafts
from .planner import plan_tasks
from .research import research_competitors
from .runtime import AgentRunController, start_agent_run

__all__ = [
    "AgentRunController", "build_drafts", "plan_tasks",
    "research_competitors", "review_drafts", "start_agent_run",
]
```

- [ ] **Step 5: Run the controller test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_runtime.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/aeo/agents/runtime.py src/aeo/agents/__init__.py tests/unit/test_agent_runtime.py
git commit -m "feat(agents): controller critic step (research->plan->build->critic->staged)"
```

---

### Task 4: Full-suite verification + end-to-end

**Files:** none (verification)

- [ ] **Step 1: Run the whole agent suite**

Run: `python -m pytest tests/unit/test_agent_critic.py tests/unit/test_agent_runtime.py tests/unit/test_agent_builder.py tests/unit/test_agent_research.py tests/unit/test_agent_planner.py tests/unit/test_agent_planner_nodes.py tests/unit/test_agent_instrument.py tests/unit/test_llm_cost.py tests/unit/test_planning_client.py tests/unit/test_agents_settings.py tests/unit/test_agent_api.py tests/unit/test_agent_worker.py -v`
Expected: all passed.

- [ ] **Step 2: Run the full project suite + linter**

Run: `python -m pytest -q`
Expected: all green (the only modified shared files are `settings.py` and `agents/*`; `validation/*` is reused unchanged).

Run: `python -m ruff check src/aeo/agents src/aeo/settings.py`
Expected: `All checks passed!`

- [ ] **Step 3: End-to-end against the live DB** (Postgres up, migrations applied)

Enqueue + drain a run with the LLM disabled (deterministic scaffold drafts + deterministic critic floors — no network):

Run: `AEO__LLM__ENABLED=false python -m aeo.cli agent Acme --domain acme.com --topic ctem`
Run: `AEO__LLM__ENABLED=false python -m aeo.cli worker` *(Ctrl-C after `job_done ... kind=agent_run`)*

Inspect the critic verdicts (replace `<id>`):

Run: `python -c "import json; from aeo.storage.repos import agent_runs as r; run=r.get('<id>'); print('steps:', [(s['seq'],s['agent'],s['status']) for s in r.steps_for('<id>')]); t=[t for t in run['result']['tasks'] if t.get('critic')][0]; print('first verdict:', json.dumps({k:t['critic'][k] for k in ('passed','independent_passed','claims_flagged','needs_review')}))"`
Expected: steps include `(4,'critic','ok')`; the first verdict prints `passed`/`independent_passed`/`claims_flagged`/`needs_review` booleans. (Scaffold drafts whose H1 is not a question will show `independent_passed: false` → `needs_review: true`, which is correct: the Critic flags them for the human.)

---

## Self-Review

**Spec coverage (against §2.2, §2.6, risk #1 of the design doc):** the rationalized Critic pipeline (Validator + Safety merged) is implemented — deterministic independent checks (`validate_page_draft`), a model-isolated adversarial auditor (`adversarial_audit` — isolation in the prompt, not the vendor, breaking the recommender's self-grading loop), and a claim/compliance auditor (`claim_audit` — the highest-stakes gate for an AEO product that writes claims onto client sites). It is **assistive, not autonomous**: the Critic annotates and flags, never publishes and never auto-rejects; the 2A human gate (`approve`/`reject`) stays the authority. Every check has a deterministic floor, preserving the deterministic-first contract. Cost is recorded onto `agent_steps` via 2B's `InstrumentedLLM` (the design's cost-blindness risk).

**Placeholder scan:** none — every code step is complete; every test shows assertions; every run step shows the command + expected output.

**Type/name consistency:** `claim_audit(text, *, llm) -> {flagged, claims, source}` and `review_drafts(graph, *, llm, origin, verify_citations, adversarial_max_attempts) -> graph` are used identically in the agent, the controller, and the tests. The controller passes `vcfg.verify_citations` / `vcfg.adversarial_max_attempts` from the existing `ValidationCfg`; the critic writes verdicts to `task['critic']` and cost to the `agent_steps` columns from 2A; sub-phase is `current_step='critic'`, so neither the `agent_runs.status` CHECK constraint nor the schema changes (no migration). The 2B `test_agent_runtime.py` is extended (not contradicted) — research→plan→build is unchanged; the critic is appended as step 4.

**Deliberately deferred (Plan 2D, not gaps):** the `AgentReviewQueue.tsx` frontend that renders these verdicts and the per-step SSE stream. Their data already exists on `GET /api/agent/run/{id}` (`result.tasks[].critic` + the step trace), so 2D is pure presentation/transport with no further backend work. A future refinement could let the Critic's `needs_review` count drive a run-level badge in the queue, but that is a UI concern for 2D.
