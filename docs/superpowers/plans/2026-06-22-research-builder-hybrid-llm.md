# Research + Builder Agents + Hybrid LLM Routing — Implementation Plan (Phase 2B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent runtime its first real LLM work — a Research agent that discovers + live-verifies competitors and a Builder agent that drafts staged page copy — on a hybrid LLM router (frontier for reasoning/drafting, local for grunt) with per-call cost accounting recorded into `agent_steps`. Everything stays staged for the human approval gate built in Phase 2A.

**Architecture:** Extends `AgentRunController` from `research → plan → build → staged`. Research wraps the existing `discover_competitors` (LLM proposal + concurrent HEAD verify, deterministic-first). Builder wraps the existing `draft_missing_page` (LLM prose with a deterministic scaffold floor; JSON-LD always built in code). A new `get_planning_client()` adds a frontier tier alongside the untouched `get_client()`/`get_bulk_client()` singletons (no risky `lru_cache` surgery). An `InstrumentedLLM` wrapper times each call and estimates tokens/cost so the Builder step persists `model`/`tokens`/`cost_usd`/`latency_ms` (the columns 2A already created) — closing the "no cost accounting" risk before scale.

**Tech Stack:** Python 3.11, httpx, FastAPI, pytest. Builds directly on Plan 2A (`agent_runs`/`agent_steps`, `AgentRunController`, `plan_tasks`). Reuses `aeo.recommender.draft.draft_missing_page` and `aeo.reference.competitor_discovery.discover_competitors` unchanged.

---

## Prerequisite

Plan 2A (`2026-06-22-agent-runtime-walking-skeleton.md`) is implemented and merged: `agent_runs`/`agent_steps` tables, `agent_runs` repo, `plan_tasks`, `AgentRunController` (plan→staged), the `AGENT_RUN` worker kind, and the `/api/agent/run` endpoints all exist and pass.

## Scope

**In scope:** hybrid LLM routing (`planning_provider` + `get_planning_client`), cost estimation (`nlp/cost.py`), the `InstrumentedLLM` wrapper, the Research agent (competitor discovery), the Builder agent (staged page drafts), the Planner extension (carry full blueprint nodes + topic so the Builder can draft), and the controller rewire to `research → plan → build → staged` with cost recorded per step.

**Out of scope — Plan 2C:** the Critic pipeline (deterministic checks + model-isolated adversarial/claim auditor on staged drafts), the `web/components/AgentReviewQueue.tsx` frontend, and per-step SSE streaming. The drafts this plan stages are reviewed via the 2A `/api/agent/run/{id}/approve|reject` endpoints; the Critic (2C) inserts a quality gate before they reach that queue.

**No new migration:** `agent_steps` already has `model`/`tokens`/`cost_usd`/`latency_ms` (from 2A). The research/build sub-phases are tracked via the free-text `current_step` column, so the `agent_runs.status` CHECK constraint is unchanged.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/aeo/settings.py` | Modify | `LLMCfg.planning_provider`; `AgentsCfg.{research_enabled, build_enabled, draft_limit}`. |
| `src/aeo/nlp/llm.py` | Modify | `get_planning_client()` — frontier tier, mirrors `get_bulk_client()`. |
| `src/aeo/nlp/cost.py` | Create | `estimate_tokens`, `estimate_cost`, model price table. |
| `src/aeo/agents/instrument.py` | Create | `InstrumentedLLM` — duck-types `LLMClient`, records `CallTrace` per call. |
| `src/aeo/agents/planner.py` | Modify | Each task carries a full `node` dict + the graph carries `topic` (so the Builder can draft). |
| `src/aeo/agents/research.py` | Create | `research_competitors(brief, *, llm, head_check)` over `discover_competitors`. |
| `src/aeo/agents/builder.py` | Create | `build_drafts(graph, *, llm, origin, limit)` over `draft_missing_page`. |
| `src/aeo/agents/runtime.py` | Modify | Controller: `research → plan → build → staged`, cost recorded per step. |
| `src/aeo/agents/__init__.py` | Modify | Re-export the new public functions. |
| `tests/unit/test_llm_cost.py` | Create | Token/cost estimation. |
| `tests/unit/test_agent_instrument.py` | Create | `InstrumentedLLM` records + passes through. |
| `tests/unit/test_planning_client.py` | Create | `get_planning_client` routing/fallback. |
| `tests/unit/test_agent_planner_nodes.py` | Create | Planner carries `node` + `topic` (2A planner tests still pass — additive). |
| `tests/unit/test_agent_research.py` | Create | Research returns verified competitors; empty without LLM. |
| `tests/unit/test_agent_builder.py` | Create | Builder attaches scaffold drafts; honors `limit`. |
| `tests/unit/test_agent_runtime.py` | Modify | Rewrite for the `research→plan→build→staged` flow + cost step. |

**Run tests** with `python -m pytest` from the repo root.

---

### Task 1: Settings — planning_provider + agent build/research flags

**Files:**
- Modify: `src/aeo/settings.py`
- Test: `tests/unit/test_planning_client.py` (Step 1 here; the client lands in Task 2)

- [ ] **Step 1: Write the failing test** (settings half only for now)

```python
# tests/unit/test_planning_client.py
"""Hybrid planning tier: LLMCfg.planning_provider + AgentsCfg build/research flags + get_planning_client()."""

from __future__ import annotations


def test_llmcfg_has_planning_provider_default_empty() -> None:
    from aeo.settings import LLMCfg

    assert LLMCfg().planning_provider == ""


def test_agentscfg_has_build_and_research_flags() -> None:
    from aeo.settings import AgentsCfg

    cfg = AgentsCfg()
    assert cfg.research_enabled is True
    assert cfg.build_enabled is True
    assert cfg.draft_limit == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_planning_client.py -v`
Expected: FAIL — `AttributeError: 'LLMCfg' object has no attribute 'planning_provider'`.

- [ ] **Step 3: Add `planning_provider` to `LLMCfg`**

In `class LLMCfg(BaseModel):`, immediately after the `bulk_provider: str = ""` line:

```python
    # Hybrid reasoning tier: the agent Planner/Builder route their frontier calls here (e.g.
    # AEO__LLM__PLANNING_PROVIDER=cloud) while the bulk audit path stays on bulk_provider and
    # the fast sync endpoints stay on `provider`. Empty = use the primary `provider`.
    planning_provider: str = ""
```

- [ ] **Step 4: Add the agent flags to `AgentsCfg`**

In `class AgentsCfg(BaseModel):` (added in Plan 2A), add:

```python
    # Phase 2B agent steps (each has a deterministic floor, so disabling only skips the LLM work).
    research_enabled: bool = True   # discover + live-verify competitors before planning
    build_enabled: bool = True      # draft staged page copy after planning
    draft_limit: int = 5            # cap drafts per run (the dominant frontier cost)
```

- [ ] **Step 5: Run test to verify the settings half passes**

Run: `python -m pytest tests/unit/test_planning_client.py::test_llmcfg_has_planning_provider_default_empty tests/unit/test_planning_client.py::test_agentscfg_has_build_and_research_flags -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/aeo/settings.py tests/unit/test_planning_client.py
git commit -m "feat(agents): planning_provider + agent build/research flags"
```

---

### Task 2: Hybrid planning client

**Files:**
- Modify: `src/aeo/nlp/llm.py`
- Test: `tests/unit/test_planning_client.py` (add the routing tests)

- [ ] **Step 1: Add the failing routing tests**

Append to `tests/unit/test_planning_client.py`:

```python
def test_get_planning_client_falls_back_to_primary_when_unset() -> None:
    from aeo.nlp import llm as llm_mod

    llm_mod.get_client.cache_clear()
    llm_mod.get_planning_client.cache_clear()
    # default test env: planning_provider unset → same client object as primary
    assert llm_mod.get_planning_client() is llm_mod.get_client()


def test_get_planning_client_routes_to_planning_provider(monkeypatch) -> None:
    from aeo.nlp import llm as llm_mod
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().llm, "provider", "ollama")
    monkeypatch.setattr(get_settings().llm, "planning_provider", "cloud")
    llm_mod.get_planning_client.cache_clear()
    client = llm_mod.get_planning_client()
    assert client.provider == "cloud"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_planning_client.py -k planning_client -v`
Expected: FAIL — `AttributeError: module 'aeo.nlp.llm' has no attribute 'get_planning_client'`.

- [ ] **Step 3: Add `get_planning_client`**

In `src/aeo/nlp/llm.py`, after `get_bulk_client()`:

```python
@lru_cache(maxsize=1)
def get_planning_client() -> LLMClient:
    """The reasoning/drafting tier for the agent layer (Planner/Builder). Routed to
    ``AEO__LLM__PLANNING_PROVIDER`` (e.g. ``cloud`` for a frontier model). Falls back to the
    primary ``get_client()`` when unset or equal to the primary provider — so a hybrid
    deployment pays for frontier reasoning without changing the fast sync endpoints."""
    cfg = get_settings().llm
    if not cfg.planning_provider or cfg.planning_provider == cfg.provider:
        return get_client()
    return LLMClient(cfg.model_copy(update={"provider": cfg.planning_provider}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_planning_client.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/nlp/llm.py tests/unit/test_planning_client.py
git commit -m "feat(agents): get_planning_client hybrid reasoning tier"
```

---

### Task 3: Cost estimation

**Files:**
- Create: `src/aeo/nlp/cost.py`
- Test: `tests/unit/test_llm_cost.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm_cost.py
"""Token/cost estimation for the agent LLM router."""

from __future__ import annotations

from aeo.nlp.cost import estimate_cost, estimate_tokens


def test_estimate_tokens_is_roughly_chars_over_four() -> None:
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("") == 1  # floor at 1 so a call never reads as 0 tokens


def test_local_model_is_free() -> None:
    assert estimate_cost("qwen2.5:3b", 1000, 1000) == 0.0


def test_cloud_model_costs_more_for_output() -> None:
    cost = estimate_cost("gemini-2.5-flash", 1000, 1000)
    assert cost > 0
    # output is priced higher than input, so output-heavy calls cost more
    assert estimate_cost("gemini-2.5-flash", 0, 1000) > estimate_cost("gemini-2.5-flash", 1000, 0)


def test_unknown_model_uses_a_default_price() -> None:
    assert estimate_cost("some-new-model", 1000, 1000) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_llm_cost.py -v`
Expected: FAIL — `ModuleNotFoundError: aeo.nlp.cost`.

- [ ] **Step 3: Write the cost module**

```python
# src/aeo/nlp/cost.py
"""Rough token/cost estimation for the agent LLM router.

The backends in ``llm.py`` don't surface provider token usage, so the agent layer estimates
it (chars/4) and prices it from a small per-model table. Good enough for budgeting + whale
detection; swap in real ``usage`` capture later if exact billing is needed. All figures are
USD per 1,000 tokens (input, output)."""

from __future__ import annotations

# (input_per_1k, output_per_1k) in USD. Local models are free (self-hosted compute).
PRICES: dict[str, tuple[float, float]] = {
    "qwen2.5:3b": (0.0, 0.0),
    "gemini-2.5-flash": (0.0003, 0.0025),
}
_DEFAULT_PRICE = (0.0005, 0.0015)  # conservative fallback for an unknown model


def estimate_tokens(text: str) -> int:
    """A cheap, provider-agnostic token estimate (≈ 4 chars/token), floored at 1."""
    return max(1, len(text) // 4)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """USD cost for a call, from the per-model price table (defaulting for unknown models)."""
    price_in, price_out = PRICES.get(model, _DEFAULT_PRICE)
    return round(tokens_in / 1000 * price_in + tokens_out / 1000 * price_out, 6)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_llm_cost.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/nlp/cost.py tests/unit/test_llm_cost.py
git commit -m "feat(agents): LLM token/cost estimation"
```

---

### Task 4: InstrumentedLLM wrapper

**Files:**
- Create: `src/aeo/agents/instrument.py`
- Test: `tests/unit/test_agent_instrument.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_instrument.py
"""InstrumentedLLM: duck-types LLMClient and records a CallTrace per call."""

from __future__ import annotations


class FakeInner:
    enabled = True
    model = "gemini-2.5-flash"

    def generate(self, prompt, system=None, *, json_mode=False):
        return "some output"

    def generate_json(self, prompt, system=None):
        return {"ok": True}


def test_records_a_call_and_passes_the_result_through() -> None:
    from aeo.agents.instrument import InstrumentedLLM

    inst = InstrumentedLLM(FakeInner())
    assert inst.enabled is True
    assert inst.model == "gemini-2.5-flash"

    out = inst.generate_json("a prompt that is reasonably long", "system text")
    assert out == {"ok": True}
    assert len(inst.calls) == 1
    call = inst.calls[0]
    assert call.model == "gemini-2.5-flash"
    assert call.tokens > 0
    assert call.cost_usd > 0
    assert call.latency_ms >= 0


def test_delegates_enabled_to_inner() -> None:
    from aeo.agents.instrument import InstrumentedLLM

    class Disabled:
        enabled = False
        model = "qwen2.5:3b"

        def generate_json(self, prompt, system=None):
            return None

    inst = InstrumentedLLM(Disabled())
    assert inst.enabled is False
    assert inst.generate_json("x") is None
    assert len(inst.calls) == 1  # the (null) attempt is still recorded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_instrument.py -v`
Expected: FAIL — `ModuleNotFoundError: aeo.agents.instrument`.

- [ ] **Step 3: Write the wrapper**

```python
# src/aeo/agents/instrument.py
"""InstrumentedLLM — a transparent wrapper that records per-call cost for the agent layer.

It duck-types the LLMClient surface the recommender/draft code uses (``enabled``, ``model``,
``generate``, ``generate_json``) and times + estimates each call into ``calls`` (a list of
CallTrace). An agent wraps its client in this, runs its drafting, then aggregates ``calls``
onto the agent_steps row — closing the 'LLM calls are fire-and-forget' cost-blindness risk.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from ..nlp.cost import estimate_cost, estimate_tokens


@dataclass(slots=True)
class CallTrace:
    model: str
    tokens: int
    cost_usd: float
    latency_ms: int


class InstrumentedLLM:
    """Wrap any LLMClient-shaped object; record a CallTrace per generate/generate_json call."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[CallTrace] = []

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._inner, "enabled", False))

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "unknown")

    def generate(self, prompt: str, system: str | None = None, *, json_mode: bool = False) -> str | None:
        return self._record(prompt, system, lambda: self._inner.generate(prompt, system, json_mode=json_mode))

    def generate_json(self, prompt: str, system: str | None = None) -> dict[str, Any] | None:
        return self._record(prompt, system, lambda: self._inner.generate_json(prompt, system))

    def _record(self, prompt: str, system: str | None, call: Any) -> Any:
        t0 = time.perf_counter()
        out = call()
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text_in = (system or "") + (prompt or "")
        text_out = out if isinstance(out, str) else (json.dumps(out, default=str) if out else "")
        tokens_in = estimate_tokens(text_in)
        tokens_out = estimate_tokens(text_out)
        self.calls.append(
            CallTrace(
                model=self.model,
                tokens=tokens_in + tokens_out,
                cost_usd=estimate_cost(self.model, tokens_in, tokens_out),
                latency_ms=latency_ms,
            )
        )
        return out

    def totals(self) -> dict[str, Any]:
        """Aggregate the recorded calls for an agent_steps row."""
        return {
            "tokens": sum(c.tokens for c in self.calls),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 6),
            "llm_calls": len(self.calls),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_instrument.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/agents/instrument.py tests/unit/test_agent_instrument.py
git commit -m "feat(agents): InstrumentedLLM per-call cost wrapper"
```

---

### Task 5: Planner carries full nodes + topic

**Files:**
- Modify: `src/aeo/agents/planner.py`
- Test: `tests/unit/test_agent_planner_nodes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_planner_nodes.py
"""Planner extension: each task carries the full blueprint node + the graph carries topic,
so the Builder can draft. The 2A planner tests still pass (these fields are additive)."""

from __future__ import annotations

from aeo.agents.planner import plan_tasks
from aeo.reference.business_input import BusinessInput


def test_graph_carries_topic() -> None:
    graph = plan_tasks(BusinessInput(name="Acme", domain="acme.com", topic="ctem"))
    assert graph["topic"]


def test_tasks_carry_a_full_node() -> None:
    graph = plan_tasks(BusinessInput(name="Acme", domain="acme.com", topic="ctem"))
    node = graph["tasks"][0]["node"]
    assert set(node) >= {"slug", "title", "page_type", "intent", "priority"}
    assert "required_entities" in node
    assert "seed_questions" in node
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_planner_nodes.py -v`
Expected: FAIL — `KeyError: 'topic'` / `KeyError: 'node'`.

- [ ] **Step 3: Extend `plan_tasks`**

In `src/aeo/agents/planner.py`, replace the body from `framework = resolve_framework(...)` through the `return {...}` with:

```python
    framework = resolve_framework(
        brief.key(), llm=llm, topic=brief.topic_hint(), category=brief.category
    )
    topic = brief.topic or framework.topic or brief.topic_hint()
    plan = plan_from_brief(brief, framework=framework, llm=llm, engine_target="generic")
    plan_d = plan.to_dict()
    profile = plan_d["profile"]
    nodes = plan_d["blueprint"]["sitemap"]

    _node_keys = (
        "slug", "title", "page_type", "intent", "cluster",
        "priority", "required_entities", "seed_questions",
    )
    tasks = [
        {
            "id": f"page:{n['slug']}",
            "kind": "content",
            "title": f"Create: {n['title']}",
            "slug": n["slug"],
            "page_type": n["page_type"],
            "priority": n["priority"],
            "status": "proposed",
            "node": {k: n.get(k) for k in _node_keys},
        }
        for n in sorted(nodes, key=lambda n: n.get("priority", 999))
    ]

    return {
        "domain": brief.key(),
        "topic": topic,
        "scenario": profile.get("scenario"),
        "headline": profile.get("headline"),
        "blueprint_pages": plan_d["blueprint"]["ideal_pages"],
        "coverage_pct": plan_d["coverage"]["pct"],
        "tasks": tasks,
    }
```

- [ ] **Step 4: Run test to verify it passes (and 2A planner tests still pass)**

Run: `python -m pytest tests/unit/test_agent_planner_nodes.py tests/unit/test_agent_planner.py -v`
Expected: all passed (the 2A assertions are a subset of the now-richer task dict).

- [ ] **Step 5: Commit**

```bash
git add src/aeo/agents/planner.py tests/unit/test_agent_planner_nodes.py
git commit -m "feat(agents): Planner carries full blueprint nodes + topic"
```

---

### Task 6: Research agent (competitor discovery)

**Files:**
- Create: `src/aeo/agents/research.py`
- Test: `tests/unit/test_agent_research.py`

- [ ] **Step 1: Write the failing test** (inject a fake LLM + fake head_check — no network)

```python
# tests/unit/test_agent_research.py
"""Research agent: discover + live-verify competitors (deterministic-first)."""

from __future__ import annotations

from aeo.agents.research import research_competitors


class FakeLLM:
    enabled = True

    def generate_json(self, prompt, system=None):
        return {"competitors": [
            {"name": "Rapid7", "domain": "rapid7.com"},
            {"name": "Tenable", "domain": "tenable.com"},
        ]}


def test_returns_verified_competitors() -> None:
    out = research_competitors(
        {"name": "Acme", "domain": "acme.com", "topic": "ctem"},
        llm=FakeLLM(), head_check=lambda domain: True,  # all reachable
    )
    assert [c["domain"] for c in out["competitors"]] == ["rapid7.com", "tenable.com"]
    assert out["dropped"] == []


def test_drops_unreachable_domains() -> None:
    out = research_competitors(
        {"name": "Acme", "domain": "acme.com", "topic": "ctem"},
        llm=FakeLLM(), head_check=lambda domain: domain == "rapid7.com",
    )
    assert [c["domain"] for c in out["competitors"]] == ["rapid7.com"]
    assert [c["domain"] for c in out["dropped"]] == ["tenable.com"]


def test_empty_without_llm() -> None:
    out = research_competitors({"name": "Acme"}, llm=None)
    assert out["competitors"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_research.py -v`
Expected: FAIL — `ModuleNotFoundError: aeo.agents.research`.

- [ ] **Step 3: Write the Research agent**

```python
# src/aeo/agents/research.py
"""Research agent — discover and live-verify a brief's competitors.

A thin agent wrapper over reference.competitor_discovery.discover_competitors: it proposes
competitors via the LLM and verifies each domain with a concurrent HEAD probe, returning only
reachable ones. Deterministic-first: no LLM (or any failure) yields an empty result, never an
exception. The verified domains are folded back into the brief so the Planner benchmarks
against real peers, and surfaced to the human in the staged run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..nlp.llm import LLMClient
from ..reference.competitor_discovery import discover_competitors


def research_competitors(
    brief: dict[str, Any],
    *,
    llm: LLMClient | None = None,
    head_check: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Return ``{'competitors': [...], 'dropped': [...], 'relaxed': bool}`` for a brief dict.

    ``head_check`` is injectable for tests (defaults to the live force-IPv4 HEAD probe)."""
    name = brief.get("name") or brief.get("domain") or "site"
    result = discover_competitors(
        name,
        brief.get("domain") or "",
        topic=brief.get("topic"),
        location=brief.get("location"),
        services=list(brief.get("services") or []),
        llm=llm,
        head_check=head_check,
    )
    return {
        "competitors": [{"name": c.name, "domain": c.domain} for c in result.verified],
        "dropped": [{"name": c.name, "domain": c.domain} for c in result.dropped],
        "relaxed": result.relaxed,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_research.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/agents/research.py tests/unit/test_agent_research.py
git commit -m "feat(agents): Research agent (competitor discovery)"
```

---

### Task 7: Builder agent (staged page drafts)

**Files:**
- Create: `src/aeo/agents/builder.py`
- Test: `tests/unit/test_agent_builder.py`

- [ ] **Step 1: Write the failing test** (offline — conftest disables the LLM, so the scaffold floor runs)

```python
# tests/unit/test_agent_builder.py
"""Builder agent: attach staged drafts to a task graph (deterministic scaffold floor)."""

from __future__ import annotations

from aeo.agents.builder import build_drafts


def _graph(n: int) -> dict:
    return {
        "topic": "ctem",
        "domain": "acme.com",
        "tasks": [
            {
                "id": f"page:/p{i}", "kind": "content", "slug": f"/p{i}",
                "page_type": "pillar", "status": "proposed",
                "node": {
                    "slug": f"/p{i}", "title": f"Page {i}", "page_type": "pillar",
                    "intent": "informational", "cluster": "core", "priority": i,
                    "required_entities": ["CVSS"], "seed_questions": ["What is it?"],
                },
            }
            for i in range(n)
        ],
    }


def test_attaches_a_scaffold_draft_without_llm() -> None:
    out = build_drafts(_graph(1), llm=None, origin="https://acme.com", limit=5)
    task = out["tasks"][0]
    assert task["status"] == "drafted"
    assert task["draft"]["draft_quality"] == "scaffold"
    assert task["draft"]["body_markdown"].startswith("# Page 0")
    assert task["draft"]["jsonld"], "JSON-LD is always built in code"


def test_respects_the_draft_limit() -> None:
    out = build_drafts(_graph(3), llm=None, origin="https://acme.com", limit=1)
    drafted = [t for t in out["tasks"] if t.get("draft")]
    assert len(drafted) == 1
    assert drafted[0]["slug"] == "/p0"  # priority order, first only


def test_skips_tasks_without_a_node() -> None:
    graph = {"topic": "ctem", "tasks": [{"id": "x", "kind": "content"}]}
    out = build_drafts(graph, llm=None, limit=5)
    assert "draft" not in out["tasks"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: aeo.agents.builder`.

- [ ] **Step 3: Write the Builder agent**

```python
# src/aeo/agents/builder.py
"""Builder agent — draft staged page copy for a planned task graph.

For each 'content' page task that carries a blueprint ``node``, it calls the existing
recommender.draft.draft_missing_page: LLM-authored prose when a model is enabled, a grounded
deterministic scaffold otherwise (JSON-LD is always built in code, never hallucinated). Drafts
are attached IN PLACE under ``task['draft']`` and the task is marked ``drafted`` — nothing is
published. Only the top ``limit`` tasks (priority order from the Planner) are drafted; drafting
is the dominant frontier cost, so it is capped.
"""

from __future__ import annotations

from typing import Any

from ..recommender.draft import draft_missing_page


def build_drafts(
    graph: dict[str, Any],
    *,
    llm: Any = None,
    origin: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Enrich ``graph`` in place: attach a staged ``draft`` to each page task (capped at
    ``limit``). Returns the same graph. ``llm`` may be an InstrumentedLLM so the caller can
    aggregate per-call cost afterward."""
    topic = graph.get("topic") or graph.get("domain") or "general"
    page_tasks = [t for t in graph.get("tasks", []) if t.get("kind") == "content" and t.get("node")]
    for task in page_tasks[: max(0, limit)]:
        draft = draft_missing_page(task["node"], topic=topic, llm=llm, origin=origin)
        task["draft"] = draft.to_payload()
        task["status"] = "drafted"
    return graph
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_builder.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/aeo/agents/builder.py tests/unit/test_agent_builder.py
git commit -m "feat(agents): Builder agent (staged page drafts)"
```

---

### Task 8: Wire the controller — research → plan → build → staged

**Files:**
- Modify: `src/aeo/agents/runtime.py`
- Modify: `src/aeo/agents/__init__.py`
- Test: `tests/unit/test_agent_runtime.py` (rewrite for the new flow)

- [ ] **Step 1: Rewrite the controller test for the new flow**

Replace the body of `tests/unit/test_agent_runtime.py` (keep the `FakeRepo`/`_row` helpers; extend `FakeRepo.append_step` already accepts `**kw`) with:

```python
# tests/unit/test_agent_runtime.py
"""AgentRunController: research → plan → build → staged, and the failure path. Injected fakes."""

from __future__ import annotations

import pytest

from aeo.settings import AgentsCfg


class FakeRepo:
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


def _ctrl(repo, *, research=None, planner=None, builder=None, cfg=None):
    from aeo.agents.runtime import AgentRunController

    return AgentRunController(
        research=research or (lambda brief, **kw: {"competitors": []}),
        planner=planner or (lambda brief: {"topic": "ctem", "tasks": [{"id": "t", "kind": "content"}]}),
        builder=builder or (lambda graph, **kw: graph),
        repo=repo,
        llm_provider=lambda: None,
        cfg=cfg or AgentsCfg(),
    )


def test_full_flow_records_research_plan_build_in_order() -> None:
    repo = FakeRepo(_row())
    research = lambda brief, **kw: {"competitors": [{"name": "R7", "domain": "rapid7.com"}]}
    planner = lambda brief: {"topic": "ctem", "tasks": [{"id": "page:/x", "kind": "content"}]}
    builder = lambda graph, **kw: {**graph, "built": True}

    out = _ctrl(repo, research=research, planner=planner, builder=builder).run("run1")

    assert out["status"] == "staged"
    assert out["result"]["built"] is True
    assert [(s["seq"], s["agent"]) for s in repo.steps] == [(1, "research"), (2, "planner"), (3, "builder")]


def test_competitors_are_folded_into_the_brief() -> None:
    repo = FakeRepo(_row())
    seen = {}

    def planner(brief):
        seen["competitors"] = brief.competitors
        return {"topic": "ctem", "tasks": []}

    research = lambda brief, **kw: {"competitors": [{"name": "R7", "domain": "rapid7.com"}]}
    _ctrl(repo, research=research, planner=planner).run("run1")
    assert seen["competitors"] == ["rapid7.com"]


def test_flags_off_runs_planner_only() -> None:
    repo = FakeRepo(_row())
    cfg = AgentsCfg(research_enabled=False, build_enabled=False)
    _ctrl(repo, cfg=cfg).run("run1")
    assert [(s["seq"], s["agent"]) for s in repo.steps] == [(1, "planner")]


def test_planner_failure_marks_failed_and_reraises() -> None:
    repo = FakeRepo(_row())

    def boom(brief):
        raise RuntimeError("planner exploded")

    cfg = AgentsCfg(research_enabled=False, build_enabled=False)
    with pytest.raises(RuntimeError, match="planner exploded"):
        _ctrl(repo, planner=boom, cfg=cfg).run("run1")
    assert repo.runs["run1"]["status"] == "failed"
    assert repo.steps[0]["status"] == "failed"
    assert repo.steps[0]["error_class"] == "RuntimeError"


def test_terminal_status_is_a_noop() -> None:
    repo = FakeRepo(_row(status="approved"))
    called = []
    _ctrl(repo, planner=lambda brief: called.append(1) or {}).run("run1")
    assert repo.runs["run1"]["status"] == "approved"
    assert called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_agent_runtime.py -v`
Expected: FAIL — `AgentRunController.__init__` got an unexpected keyword `research`.

- [ ] **Step 3: Rewrite the controller**

Replace the `AgentRunController` class in `src/aeo/agents/runtime.py` with this (keep `brief_from_dict`, add the new imports + `_TERMINAL`):

```python
from __future__ import annotations

from typing import Any

from ..reference.business_input import BusinessInput
from ..storage.repos import agent_runs as agent_runs_repo
from .builder import build_drafts
from .instrument import InstrumentedLLM
from .planner import plan_tasks
from .research import research_competitors

_TERMINAL = frozenset({"staged", "approved", "rejected", "failed", "cancelled"})


def brief_from_dict(d: dict[str, Any]) -> BusinessInput:
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


def _planning_client():
    from ..nlp.llm import get_planning_client

    return get_planning_client()


class AgentRunController:
    def __init__(
        self,
        *,
        research=research_competitors,
        planner=plan_tasks,
        builder=build_drafts,
        repo=agent_runs_repo,
        brief_builder=brief_from_dict,
        llm_provider=_planning_client,
        cfg=None,
    ) -> None:
        self._research = research
        self._planner = planner
        self._builder = builder
        self._repo = repo
        self._brief = brief_builder
        self._llm_provider = llm_provider
        self._cfg = cfg

    def run(self, run_id: str) -> dict[str, Any]:
        """Drive a run research → plan → build → staged. Each step has a deterministic floor;
        idempotent (a terminal run is a no-op under at-least-once delivery)."""
        from ..settings import get_settings

        row = self._repo.get(run_id)
        if row is None:
            raise ValueError(f"unknown agent run: {run_id!r}")
        if row["status"] in _TERMINAL:
            return row

        cfg = self._cfg or get_settings().agents
        brief_dict = dict(row.get("brief") or {})
        seq = 0

        # ── research (best-effort; deterministic-first) ──
        if cfg.research_enabled:
            self._repo.set_status(run_id, "planning", current_step="research")
            try:
                res = self._research(brief_dict, llm=self._llm_provider())
            except Exception:  # research never blocks a run
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
            origin = f"https://{brief.key()}" if brief.domain else None
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

        self._repo.set_status(run_id, "staged", current_step="review", result=graph)
        return self._repo.get(run_id)
```

Keep the existing `start_agent_run(...)` function at the bottom of the file unchanged.

- [ ] **Step 4: Re-export the new agents**

In `src/aeo/agents/__init__.py`, update the imports/exports:

```python
from .builder import build_drafts
from .planner import plan_tasks
from .research import research_competitors
from .runtime import AgentRunController, start_agent_run

__all__ = ["AgentRunController", "build_drafts", "plan_tasks", "research_competitors", "start_agent_run"]
```

- [ ] **Step 5: Run the controller test to verify it passes**

Run: `python -m pytest tests/unit/test_agent_runtime.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/aeo/agents/runtime.py src/aeo/agents/__init__.py tests/unit/test_agent_runtime.py
git commit -m "feat(agents): controller research->plan->build->staged + cost recording"
```

---

### Task 9: Full-suite verification + end-to-end

**Files:** none (verification)

- [ ] **Step 1: Run the whole agent suite**

Run: `python -m pytest tests/unit/test_agents_settings.py tests/unit/test_agent_runs_schema.py tests/unit/test_agent_runs_repo.py tests/unit/test_agent_planner.py tests/unit/test_agent_planner_nodes.py tests/unit/test_agent_runtime.py tests/unit/test_agent_worker.py tests/unit/test_agent_api.py tests/unit/test_agent_research.py tests/unit/test_agent_builder.py tests/unit/test_agent_instrument.py tests/unit/test_llm_cost.py tests/unit/test_planning_client.py -v`
Expected: all passed.

- [ ] **Step 2: Run the full project suite + linter**

Run: `python -m pytest -q`
Expected: all green (existing suite unaffected — the only modified shared files are `settings.py`, `nlp/llm.py`, `agents/*`).

Run: `python -m ruff check src/aeo/agents src/aeo/nlp/cost.py src/aeo/nlp/llm.py src/aeo/settings.py`
Expected: `All checks passed!`

- [ ] **Step 3: End-to-end against the live DB** (Postgres up, migrations applied)

Enqueue a run with the LLM disabled (deterministic scaffold drafts — no network, no cost):

Run: `AEO__LLM__ENABLED=false python -m aeo.cli agent Acme --domain acme.com --topic ctem`
Expected: `agent run <id> queued ...`

Drain one job:

Run: `AEO__LLM__ENABLED=false python -m aeo.cli worker` *(Ctrl-C after `job_done ... kind=agent_run`)*

Inspect the staged run + its steps (replace `<id>`):

Run: `python -c "import json; from aeo.storage.repos import agent_runs as r; run=r.get('<id>'); print(run['status'], run['current_step']); print([(s['seq'],s['agent'],s['status']) for s in r.steps_for('<id>')]); print('drafted:', sum(1 for t in run['result']['tasks'] if t.get('draft')))"`
Expected: `staged review`, steps `[(1,'research','ok'),(2,'planner','ok'),(3,'builder','ok')]` (research returns 0 verified with the LLM off, which is fine), and `drafted: <N>` ≥ 1 with `draft_quality='scaffold'`.

> To exercise the frontier path, set `AEO__LLM__PROVIDER=cloud`, `AEO__LLM__PLANNING_PROVIDER=cloud`, and `AEO__LLM__CLOUD_API_KEY=...`; the builder step then records non-null `model`/`tokens`/`cost_usd`.

- [ ] **Step 4: Approve the staged run via the API (the 2A gate)**

With the API running (`python -m aeo.cli serve`):

Run: `curl -s -X POST localhost:8000/api/agent/run/<id>/approve`
Expected: `{"run_id":"<id>","status":"approved"}` (or `{"detail":"run is approved, not staged"}` if already decided).

---

## Self-Review

**Spec coverage (against §2.2–§2.10 of the design doc):** the Research and Builder agents from the rationalized roster are implemented over their mapped seams (`discover_competitors`, `draft_missing_page`); hybrid LLM posture is realized via `get_planning_client()` (frontier) alongside the untouched `get_bulk_client()` (local) — no `lru_cache` surgery, so the deterministic-first contract on every existing call site is preserved; cost accounting (the design's #2 risk and a Phase-1 "must") lands as `InstrumentedLLM` + `nlp/cost.py`, persisting `model`/`tokens`/`cost_usd` onto `agent_steps`. The Builder stages drafts and never publishes (assistive + gate); JSON-LD stays deterministic (`draft.py` builds it in code — no hallucinated schema). The `draft_limit` cap addresses the "unbounded drafting cost" driver.

**Placeholder scan:** none — every code step is complete; every test shows assertions; every run step shows the command + expected output.

**Type/name consistency:** `get_planning_client()`, `estimate_tokens`/`estimate_cost`, `InstrumentedLLM(inner).{enabled,model,generate,generate_json,totals,calls}`, `research_competitors(brief, *, llm, head_check) -> {competitors,dropped,relaxed}`, `build_drafts(graph, *, llm, origin, limit) -> graph`, and the extended `plan_tasks` (`graph['topic']` + `task['node']`) are used identically across the agents, the controller, and the tests. The controller writes `model`/`tokens`/`cost_usd`/`latency_ms` into the exact `agent_steps` columns created by 2A's migration 0019; sub-phases use the free-text `current_step` (research/plan/build/review), so the `agent_runs.status` CHECK constraint from 2A is unchanged (no new migration). The 2A `test_agent_runtime.py` is rewritten here because the controller flow changed; the 2A planner tests still pass because the new task fields are additive.

**Deliberately deferred (Plan 2C, not gaps):** the Critic pipeline (deterministic + model-isolated adversarial/claim audit) that should gate staged drafts before human review; the `AgentReviewQueue.tsx` frontend; per-step SSE. Research cost is recorded as a verified-count only (a single LLM call, left uninstrumented in 2B); instrumenting it is a trivial follow-up if research volume grows.
