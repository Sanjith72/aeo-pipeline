"""
Strategy clustering (R2-5) — group the plan's tasks by difficulty/maturity grade.

The interactive plan (``packager.build_plan``) is ordered by priority; the Strategy tab
offers the *complementary* view: the same tasks bucketed by how hard / how mature they are
(foundation → growth → advanced), each bucket carrying a short readme so the owner knows
what the group is, why it matters, and how to approach it.

Difficulty is read off each task's ``effort`` grade (the deterministic, stable signal the
packager already assigns per page type). Grouping is therefore pure and reproducible — the
same input set always yields the same groups, in the same order. The optional ``llm`` only
*enriches* the readme prose; with the LLM off (the default) the deterministic readmes are
used, so the feature is fully unit-testable offline. The readme reuses the R1 #12
what/why/how contract (mirrors ``current_state``/``action``/``how_to``).
"""

from __future__ import annotations

from typing import Any

from ..logging import get_logger
from ..nlp.llm import LLMClient

log = get_logger(__name__)

# Maturity ladder: foundation first (the floor everyone needs), then growth, then advanced.
GRADE_ORDER = ("foundation", "growth", "advanced")

# A task's effort grade IS its difficulty signal — map it to a maturity grade.
_EFFORT_TO_GRADE: dict[str, str] = {"low": "foundation", "medium": "growth", "high": "advanced"}

GRADE_META: dict[str, dict[str, str]] = {
    "foundation": {
        "title": "Foundation",
        "difficulty": "Beginner",
        "why": "These are the basics AI assistants check first — get them right and everything "
               "above builds on solid ground.",
    },
    "growth": {
        "title": "Growth",
        "difficulty": "Intermediate",
        "why": "With the basics in place, these deepen your coverage and authority on the topics "
               "that win answers.",
    },
    "advanced": {
        "title": "Advanced",
        "difficulty": "Advanced",
        "why": "The heavier, higher-craft work — pillar content and flagship pages that set you "
               "apart once the rest is live.",
    },
}


def _deterministic_readme(grade: str, tasks: list[dict[str, Any]]) -> dict[str, str]:
    """The what/why/how readme used by default (and as the LLM-enrich fallback)."""
    meta = GRADE_META[grade]
    n = len(tasks)
    difficulty = meta["difficulty"].lower()
    return {
        "what": f"{n} {difficulty}-level task{'' if n == 1 else 's'} grouped by difficulty.",
        "why": meta["why"],
        "how": "Work through them top to bottom — each task below carries its own step-by-step "
               "guidance and a ready-made prompt.",
    }


_LLM_SYSTEM = (
    "You write short, plain-language guidance for small-business owners. Return STRICT JSON "
    "with exactly the keys 'what', 'why', 'how' — each a single concise sentence."
)


def _llm_readme(grade: str, tasks: list[dict[str, Any]], llm: LLMClient) -> dict[str, str] | None:
    """Ask the LLM to tailor the what/why/how readme. Returns None on any failure so the
    caller falls back to the deterministic readme (the system never blocks on the LLM)."""
    meta = GRADE_META[grade]
    labels = "\n".join(f"- {t.get('label', t.get('id', ''))}" for t in tasks[:12])
    prompt = (
        f"This is the '{meta['title']}' ({meta['difficulty']}) group of an AEO action plan. "
        f"It contains {len(tasks)} task(s):\n{labels}\n\n"
        "Write a readme for this group as JSON with keys what/why/how."
    )
    try:
        data = llm.generate_json(prompt, _LLM_SYSTEM)
    except Exception as exc:  # the readme must never break the bundle
        log.warning("strategy_readme_llm_failed", grade=grade, error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    out = {k: str(data[k]).strip() for k in ("what", "why", "how") if data.get(k)}
    return out if {"what", "why", "how"} <= out.keys() else None


def cluster_by_grade(
    tasks: list[dict[str, Any]], *, llm: LLMClient | None = None
) -> list[dict[str, Any]]:
    """Cluster plan tasks into maturity grades. Deterministic + stable: the same input
    yields the same groups in ``GRADE_ORDER``, each with a readme and its linked tasks."""
    buckets: dict[str, list[dict[str, Any]]] = {g: [] for g in GRADE_ORDER}
    for t in tasks:
        grade = _EFFORT_TO_GRADE.get(str(t.get("effort", "medium")), "growth")
        buckets[grade].append(t)

    groups: list[dict[str, Any]] = []
    for grade in GRADE_ORDER:
        items = buckets[grade]
        if not items:
            continue
        readme = None
        if llm is not None and llm.enabled:
            readme = _llm_readme(grade, items, llm)
        if readme is None:
            readme = _deterministic_readme(grade, items)
        meta = GRADE_META[grade]
        groups.append({
            "grade": grade,
            "title": meta["title"],
            "difficulty": meta["difficulty"],
            "readme": readme,
            "task_ids": [t["id"] for t in items],
            "tasks": items,
        })
    return groups


def build_strategy(plan: dict[str, Any], *, llm: LLMClient | None = None) -> dict[str, Any]:
    """Strategy view over an already-built ``plan`` (``packager.build_plan`` output):
    flatten its phased tasks, then cluster by difficulty/maturity grade."""
    tasks = [t for phase in plan.get("phases", []) for t in phase.get("tasks", [])]
    groups = cluster_by_grade(tasks, llm=llm)
    return {
        "groups": groups,
        "grades": [g["grade"] for g in groups],
        "total": len(tasks),
    }


def strategy_readme_markdown(strategy: dict[str, Any]) -> str:
    """Render the strategy groups as a single readme markdown asset (for the kit/zip)."""
    lines = ["# Strategy — your plan by difficulty", ""]
    for g in strategy.get("groups", []):
        r = g.get("readme", {})
        lines += [
            f"## {g['title']} ({g['difficulty']}) — {len(g.get('tasks', []))} task(s)",
            "",
            f"**What:** {r.get('what', '')}",
            f"**Why:** {r.get('why', '')}",
            f"**How:** {r.get('how', '')}",
            "",
        ]
        for t in g.get("tasks", []):
            lines.append(f"- {t.get('label', t.get('id', ''))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
