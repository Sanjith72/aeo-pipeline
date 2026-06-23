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
