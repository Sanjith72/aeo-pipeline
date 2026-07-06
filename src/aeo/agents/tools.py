"""
Tool registry — the schema-documented capabilities the ReAct agent can invoke.

Every tool wraps an existing deterministic service seam (research, planner, builder,
critic, discovery, scoring, semantic search); the *tools* stay deterministic and
testable, and the *orchestration* — which tool, when, with what arguments — is what the
LLM decides in ``agents/react.py``. That keeps the codebase's deterministic-first
contract: a tool failure becomes an error observation the agent can react to, never an
exception into the run.

Tools operate on a shared :class:`ToolContext`, where rich state (the brief, the staged
task graph) lives between steps; observations returned to the model are compact,
JSON-serializable summaries — never multi-hundred-KB payloads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..logging import get_logger

log = get_logger(__name__)

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass
class ToolContext:
    """Shared state for one agent run. ``state`` holds the rich artifacts (brief, task
    graph); ``done``/``outcome`` are set by a terminal tool (stage_plan)."""

    state: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    outcome: dict[str, Any] | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema ({"type": "object", "properties": ..., "required": ...})
    handler: Callable[..., Any]  # handler(ctx: ToolContext, **args) -> JSON-serializable


class ToolRegistry:
    """Named tools + argument validation + exception-safe invocation."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        """OpenAI-function-style specs — what the agent prompt advertises."""
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self._tools.values()
        ]

    def _validate(self, tool: Tool, args: dict[str, Any]) -> str | None:
        """Basic JSON-Schema validation (required + primitive types + no unknown keys).
        Returns an error message or None — errors go back to the model as observations
        so it can self-correct."""
        props: dict[str, Any] = tool.parameters.get("properties", {})
        required: list[str] = tool.parameters.get("required", [])
        for name in required:
            if name not in args:
                return f"missing required argument {name!r}"
        for name, value in args.items():
            if name not in props:
                return f"unknown argument {name!r} — allowed: {sorted(props)}"
            expected = _JSON_TYPES.get(props[name].get("type", ""))
            if expected and not isinstance(value, expected):
                return f"argument {name!r} must be {props[name]['type']}"
            if isinstance(value, bool) and props[name].get("type") in ("integer", "number"):
                return f"argument {name!r} must be {props[name]['type']}"
        return None

    def invoke(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run one tool. Always returns an observation dict — ``{"error": ...}`` on any
        failure (unknown tool, bad args, handler exception) so the loop never breaks."""
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}", "available_tools": self.names()}
        problem = self._validate(tool, args)
        if problem is not None:
            return {"error": problem, "tool": name}
        try:
            out = tool.handler(ctx, **args)
        except Exception as exc:
            log.warning("tool_failed", tool=name, error=str(exc), error_class=type(exc).__name__)
            return {"error": f"{type(exc).__name__}: {exc}", "tool": name}
        return out if isinstance(out, dict) else {"result": out}


# ─── the default planning registry ─────────────────────────────────────────────
# Wraps the same injectable seams AgentRunController uses, so tests can fake them and
# the ladder/react modes share one implementation of each capability.


def _task_summary(graph: dict[str, Any] | None, limit: int = 8) -> dict[str, Any]:
    if not graph:
        return {"task_count": 0}
    tasks = graph.get("tasks", [])
    return {
        "task_count": len(tasks),
        "coverage_pct": graph.get("coverage_pct"),
        "scenario": graph.get("scenario"),
        "top_tasks": [
            {"id": t.get("id"), "title": t.get("title"),
             "priority": t.get("priority"), "status": t.get("status")}
            for t in tasks[:limit]
        ],
    }


def planning_registry(
    *,
    research: Callable[..., dict[str, Any]],
    planner: Callable[..., dict[str, Any]],
    builder: Callable[..., dict[str, Any]],
    critic: Callable[..., dict[str, Any]],
    brief_builder: Callable[[dict[str, Any]], Any],
    llm: Any,
    draft_limit: int = 5,
    verify_citations: bool = False,
    adversarial_max_attempts: int = 3,
    allow_network: bool = True,
) -> ToolRegistry:
    """The tools for one assistive-copilot planning run. ``ctx.state`` must carry
    ``brief`` (dict); tools maintain ``graph``/``origin`` as they work."""

    def _origin(ctx: ToolContext) -> str | None:
        brief = ctx.state.get("brief") or {}
        return f"https://{brief['domain']}" if brief.get("domain") else None

    def research_competitors_tool(ctx: ToolContext) -> dict[str, Any]:
        res = research(dict(ctx.state.get("brief") or {}), llm=llm)
        competitors = res.get("competitors") or []
        if competitors:
            ctx.state["brief"] = {
                **(ctx.state.get("brief") or {}),
                "competitors": [c.get("domain") or c.get("name") for c in competitors],
            }
        return {
            "verified": [c.get("domain") or c.get("name") for c in competitors],
            "dropped": len(res.get("dropped") or []),
        }

    def plan_pages_tool(ctx: ToolContext) -> dict[str, Any]:
        brief = brief_builder(dict(ctx.state.get("brief") or {}))
        graph = planner(brief)
        ctx.state["graph"] = graph
        return _task_summary(graph)

    def draft_pages_tool(ctx: ToolContext, limit: int = 0) -> dict[str, Any]:
        graph = ctx.state.get("graph")
        if not graph:
            return {"error": "no plan yet — call plan_pages first"}
        cap = min(limit, draft_limit) if limit > 0 else draft_limit
        graph = builder(graph, llm=llm, origin=_origin(ctx), limit=cap)
        ctx.state["graph"] = graph
        drafted = sum(1 for t in graph.get("tasks", []) if t.get("draft"))
        return {"drafted": drafted, **_task_summary(graph)}

    def critique_drafts_tool(ctx: ToolContext) -> dict[str, Any]:
        graph = ctx.state.get("graph")
        if not graph:
            return {"error": "no plan yet — call plan_pages first"}
        graph = critic(
            graph, llm=llm, origin=_origin(ctx),
            verify_citations=verify_citations,
            adversarial_max_attempts=adversarial_max_attempts,
        )
        ctx.state["graph"] = graph
        flagged = [
            {"id": t.get("id"), "reasons": t.get("critic", {}).get("reasons")}
            for t in graph.get("tasks", []) if t.get("critic", {}).get("needs_review")
        ]
        return {
            "reviewed": sum(1 for t in graph.get("tasks", []) if t.get("critic")),
            "flagged": flagged,
        }

    def inspect_task_tool(ctx: ToolContext, task_id: str) -> dict[str, Any]:
        graph = ctx.state.get("graph") or {}
        for t in graph.get("tasks", []):
            if t.get("id") == task_id:
                out = {k: t.get(k) for k in ("id", "title", "slug", "priority", "status", "node")}
                critic_note = t.get("critic")
                if critic_note:
                    out["critic"] = critic_note
                if t.get("draft"):
                    draft = t["draft"]
                    out["draft_preview"] = {
                        "h1": draft.get("h1"),
                        "sections": [s.get("heading") for s in draft.get("sections", [])][:10],
                    }
                return out
        return {"error": f"no task {task_id!r}", "known_ids": [t.get("id") for t in graph.get("tasks", [])][:20]}

    def revise_task_tool(
        ctx: ToolContext, task_id: str,
        title: str = "", priority: int = 0, drop: bool = False,
    ) -> dict[str, Any]:
        graph = ctx.state.get("graph")
        if not graph:
            return {"error": "no plan yet — call plan_pages first"}
        tasks = graph.get("tasks", [])
        for i, t in enumerate(tasks):
            if t.get("id") == task_id:
                if drop:
                    tasks.pop(i)
                    return {"dropped": task_id, "task_count": len(tasks)}
                if title:
                    t["title"] = title
                if priority > 0:
                    t["priority"] = priority
                    tasks.sort(key=lambda x: x.get("priority", 999))
                return {"revised": task_id, "title": t.get("title"), "priority": t.get("priority")}
        return {"error": f"no task {task_id!r}"}

    def stage_plan_tool(ctx: ToolContext, summary: str) -> dict[str, Any]:
        graph = ctx.state.get("graph")
        if not graph or not graph.get("tasks"):
            return {"error": "cannot stage an empty plan — call plan_pages first"}
        ctx.done = True
        ctx.outcome = {"summary": summary, **_task_summary(graph)}
        return {"staged": True, "task_count": len(graph.get("tasks", []))}

    def discover_site_tool(ctx: ToolContext, max_urls: int = 50) -> dict[str, Any]:
        brief = ctx.state.get("brief") or {}
        domain = brief.get("domain")
        if not domain:
            return {"error": "the brief has no domain — this is a no-website plan"}
        import asyncio

        from ..crawl.discovery import discover

        # Tools run in worker threads (never inside a running loop), so asyncio.run is safe.
        result = asyncio.run(discover(domain, max_urls=max(1, min(max_urls, 200))))
        listing = [u.url for u in result.urls]
        ctx.state["discovered_urls"] = listing
        return {"source": result.source, "url_count": len(listing), "urls": listing[:25]}

    def semantic_search_tool(ctx: ToolContext, query: str, limit: int = 5) -> dict[str, Any]:
        from ..storage.repos import embeddings as embeddings_repo

        if not embeddings_repo.available():
            return {"error": "semantic search unavailable (no pgvector) — reason from page titles instead"}
        vector = llm.embed(query) if hasattr(llm, "embed") else None
        if vector is None:
            return {"error": "no embeddings provider available — reason from page titles instead"}
        hits = embeddings_repo.search(vector, limit=max(1, min(limit, 20)))
        return {"matches": [
            {"url": h["url"], "similarity": round(h["similarity"], 3)} for h in hits
        ]}

    tools = [
        Tool(
            name="research_competitors",
            description=(
                "Discover and live-verify competitors for the business brief. Folds the "
                "verified domains back into the brief so later planning benchmarks against "
                "real peers. Use before plan_pages when the brief lists no competitors."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=research_competitors_tool,
        ),
        Tool(
            name="plan_pages",
            description=(
                "Generate the ideal-site blueprint and the task graph (one create-page task "
                "per blueprint node, priority-ordered) from the current brief. Stores the "
                "graph; returns a summary with task_count, coverage_pct and the top tasks."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=plan_pages_tool,
        ),
        Tool(
            name="draft_pages",
            description=(
                "Draft ready-to-publish page copy (H1, sections, body, JSON-LD) for the "
                "top-priority planned tasks. Drafting is the dominant cost, so it is capped."
            ),
            parameters={
                "type": "object",
                "properties": {"limit": {
                    "type": "integer",
                    "description": "max pages to draft this call (0 = the configured cap)",
                }},
                "required": [],
            },
            handler=draft_pages_tool,
        ),
        Tool(
            name="critique_drafts",
            description=(
                "Run the model-isolated adversarial critic over the drafts: it tries to refute "
                "claims and checks citations. Returns which tasks were flagged and why — act "
                "on flags with revise_task or by re-drafting before staging."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=critique_drafts_tool,
        ),
        Tool(
            name="inspect_task",
            description="Fetch one task's full detail (node, draft preview, critic verdict) by id.",
            parameters={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
            handler=inspect_task_tool,
        ),
        Tool(
            name="revise_task",
            description=(
                "Edit one planned task: retitle it, change its priority (re-sorts the plan), "
                "or drop it entirely (e.g. when the critic flagged it as unsupportable)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "priority": {"type": "integer"},
                    "drop": {"type": "boolean"},
                },
                "required": ["task_id"],
            },
            handler=revise_task_tool,
        ),
        Tool(
            name="stage_plan",
            description=(
                "TERMINAL: submit the finished plan for human review. Call this exactly once, "
                "after the plan (and any drafts/critique) is as good as you can make it."
            ),
            parameters={
                "type": "object",
                "properties": {"summary": {
                    "type": "string",
                    "description": "2-3 sentences for the human reviewer: what was planned and why",
                }},
                "required": ["summary"],
            },
            handler=stage_plan_tool,
        ),
        Tool(
            name="semantic_search",
            description=(
                "Vector-search previously indexed site content (pgvector) for pages related "
                "to a query — grounding for coverage decisions. Degrades gracefully when "
                "unavailable."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=semantic_search_tool,
        ),
    ]
    if allow_network:
        tools.append(Tool(
            name="discover_site",
            description=(
                "Inventory the brief's existing website (sitemap-first, then shallow BFS) "
                "and return its URLs — use it to avoid planning pages that already exist. "
                "Only works when the brief has a domain."
            ),
            parameters={
                "type": "object",
                "properties": {"max_urls": {"type": "integer"}},
                "required": [],
            },
            handler=discover_site_tool,
        ))
    return ToolRegistry(tools)
