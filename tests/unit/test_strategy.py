"""
R2-5 — strategy clustering (group plan tasks by difficulty/maturity grade).

Pure + offline: clustering is deterministic (effort grade is the difficulty signal), the
LLM only enriches readme prose, so every assertion here runs with the LLM off.
"""

from __future__ import annotations

from types import SimpleNamespace

from aeo.report.packager import build_plan
from aeo.report.strategy import (
    GRADE_ORDER,
    build_strategy,
    cluster_by_grade,
    strategy_readme_markdown,
)


def _pnode(slug: str, title: str, page_type: str, priority: float) -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug, title=title, page_type=page_type, intent="informational",
        journey_stage="awareness", cluster=None, priority=priority,
        seed_questions=["What is X?"], required_entities=["X"],
    )


def _mixed_nodes() -> list[SimpleNamespace]:
    # page-type -> effort: contact=low, blog=medium, pillar=high (see packager._EFFORT_BY_TYPE)
    return [
        _pnode("/contact", "Contact", "contact", 0.9),
        _pnode("/blog/a", "Blog A", "blog", 0.7),
        _pnode("/what-is-x", "What is X", "pillar", 0.95),
    ]


def _tasks() -> list[dict]:
    plan = build_plan(_mixed_nodes(), "dev")
    return [t for p in plan["phases"] for t in p["tasks"]]


def test_clusters_by_effort_grade_in_maturity_order():
    groups = cluster_by_grade(_tasks())
    grades = [g["grade"] for g in groups]
    assert grades == ["foundation", "growth", "advanced"]  # GRADE_ORDER, empties dropped
    assert grades == [g for g in GRADE_ORDER if g in grades]


def test_each_group_has_a_readme_and_linked_tasks():
    for g in cluster_by_grade(_tasks()):
        assert set(g["readme"]) == {"what", "why", "how"}
        assert all(g["readme"][k].strip() for k in ("what", "why", "how"))
        assert g["task_ids"] == [t["id"] for t in g["tasks"]]
        assert len(g["tasks"]) >= 1


def test_tasks_land_in_the_grade_matching_their_effort():
    by_grade = {g["grade"]: g for g in cluster_by_grade(_tasks())}
    assert all(t["effort"] == "low" for t in by_grade["foundation"]["tasks"])
    assert all(t["effort"] == "medium" for t in by_grade["growth"]["tasks"])
    assert all(t["effort"] == "high" for t in by_grade["advanced"]["tasks"])


def test_clustering_is_stable_for_a_fixed_input():
    assert cluster_by_grade(_tasks()) == cluster_by_grade(_tasks())


def test_build_strategy_flattens_the_plan():
    plan = build_plan(_mixed_nodes(), "dev")
    strat = build_strategy(plan)
    assert strat["total"] == len(_tasks())
    assert strat["grades"] == [g["grade"] for g in strat["groups"]]
    # every plan task is accounted for across the groups, exactly once
    all_ids = [tid for g in strat["groups"] for tid in g["task_ids"]]
    assert sorted(all_ids) == sorted(t["id"] for t in _tasks())


def test_empty_plan_yields_no_groups():
    assert build_strategy({"phases": []}) == {"groups": [], "grades": [], "total": 0}


def test_readme_markdown_lists_every_group_and_task():
    md = strategy_readme_markdown(build_strategy(build_plan(_mixed_nodes(), "dev")))
    assert "# Strategy" in md
    for grade in ("Foundation", "Growth", "Advanced"):
        assert grade in md
