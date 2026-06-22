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
