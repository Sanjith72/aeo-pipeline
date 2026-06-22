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
