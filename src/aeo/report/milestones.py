"""
Plan → Implementation Milestones (pure).

Turns the phased "Final Plan" (:func:`aeo.report.packager.build_plan`) into the
milestone/task specs the dashboard persists and the weekly crawl verifies. Each plan
phase becomes one milestone; each plan task becomes one milestone task, carrying a
*verification signal* derived from its stable id:

  * ``page:<slug>``  → ``verify_kind="page"``,  target = the slug. The weekly crawl
    marks it verified once a page at that slug (or a heading/offering matching its
    title) is live on the site.
  * ``vis:<x>``      → ``verify_kind="manual"``. Off-site visibility wins (Google
    Business Profile, reviews) have no on-site signal, so they stay owner-attested.

Pure + deterministic: the same plan yields the same specs, in order. No DB, no network —
the repo layer (:mod:`aeo.storage.repos.milestones`) persists what this produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Human-facing milestone titles per phase — the "roadmap" framing the dashboard shows,
# layered on top of build_plan's time-based phase keys + blurbs. Mirrors the web app's
# lib/phases.PHASE_DISPLAY (the sync upserts titles, so edits here propagate to saved rows).
_PHASE_MILESTONE: dict[str, dict[str, str]] = {
    "week_1": {"title": "Quick Wins", "blurb": "Fast, high-leverage fixes — do these first."},
    "week_2_4": {"title": "Foundation", "blurb": "Make your core pages solid, complete, and trustworthy."},
    "later": {"title": "Growth & Scale", "blurb": "Longer-term moves that compound over time."},
}


@dataclass(slots=True)
class TaskSpec:
    task_key: str
    label: str
    action_required: str
    how_to: str
    verify_kind: str  # page | service | heading | manual
    verify_target: str | None
    position: int
    # Carried through from build_plan so the dashboard's "Show me how" expander can show the
    # same superset the no-domain plan view has. Defaulted (kwargs construction) + nullable:
    # current_state is plain context; prompts is the {ai, human} pair page tasks carry (None
    # for off-site visibility tasks, which have no implementation prompt).
    current_state: str = ""
    prompts: dict[str, Any] | None = None


@dataclass(slots=True)
class MilestoneSpec:
    milestone_key: str
    title: str
    blurb: str
    position: int
    tasks: list[TaskSpec] = field(default_factory=list)


def _verify_signal(task: dict[str, Any]) -> tuple[str, str | None]:
    """Map a plan task id to (verify_kind, verify_target) — what the crawl looks for.

    Page tasks key on their slug (``page:/services/x`` → the page must exist live);
    everything else (visibility wins) has no on-site signal, so it's owner-attested."""
    task_id = str(task.get("id", ""))
    if task_id.startswith("page:"):
        slug = task_id[len("page:"):].strip()
        return "page", slug or None
    return "manual", None


def plan_to_milestones(plan: dict[str, Any]) -> list[MilestoneSpec]:
    """Convert ``build_plan`` output (phases → tasks) into ordered milestone specs.

    Unknown phase keys fall back to a generic milestone titled after the phase, so the
    converter never drops tasks if the packager grows a new phase."""
    specs: list[MilestoneSpec] = []
    for m_pos, phase in enumerate(plan.get("phases", []) or []):
        key = str(phase.get("key") or phase.get("title") or f"phase_{m_pos}")
        meta = _PHASE_MILESTONE.get(key)
        title = meta["title"] if meta else str(phase.get("title") or key)
        blurb = meta["blurb"] if meta else str(phase.get("blurb") or "")
        tasks: list[TaskSpec] = []
        for t_pos, task in enumerate(phase.get("tasks", []) or []):
            kind, target = _verify_signal(task)
            prompts = task.get("prompts")
            tasks.append(
                TaskSpec(
                    task_key=str(task.get("id", "")),
                    label=str(task.get("label", "")),
                    action_required=str(task.get("action_required", "")),
                    how_to=str(task.get("how_to", "")),
                    verify_kind=kind,
                    verify_target=target,
                    position=t_pos,
                    current_state=str(task.get("current_state") or ""),
                    prompts=prompts if isinstance(prompts, dict) else None,
                )
            )
        specs.append(
            MilestoneSpec(milestone_key=key, title=title, blurb=blurb, position=m_pos, tasks=tasks)
        )
    return specs
