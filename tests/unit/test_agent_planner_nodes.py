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
