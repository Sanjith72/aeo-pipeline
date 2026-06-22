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
