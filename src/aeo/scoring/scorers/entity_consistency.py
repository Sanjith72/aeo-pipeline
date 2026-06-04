"""
Criterion 4 — Entity Consistency.

Ratio of canonical entity mentions to first-person mentions ("we/our/us"),
mapped to tiers {1:0, 2:0.5, 3:1.0, 4:1.5, 5:2.5}. The audit found Securin
pages run first-person 2:1 over the brand name — exactly the tier-2 failure
this catches.
"""

from __future__ import annotations

from ...storage.models import CriterionScore
from ..result import ScoreContext, tier_from_thresholds


def score(ctx: ScoreContext) -> CriterionScore:
    crit = ctx.rubric.get("entity_consistency")
    tiers = crit.cfg.get("tiers", {1: 0.0, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.5})

    ent = ctx.bundle.get("entities", {}) or {}
    ratio = ent.get("ratio")  # None when first_person_count == 0
    entity_count = int(ent.get("entity_count", 0) or 0)
    first_person = int(ent.get("first_person_count", 0) or 0)
    primary = ent.get("primary") or {}

    if entity_count == 0 and first_person == 0:
        value = 1
        note = "Page never names the entity"
    elif ratio is None:
        # Entity present, zero first-person → fully entity-dominant.
        value = 5
        note = "Entity named with no first-person language"
    else:
        value = tier_from_thresholds(ratio, tiers)
        note = f"entity:first-person ratio = {ratio:.2f}"

    return CriterionScore(
        name="entity_consistency",
        value=value,
        evidence={
            "primary_entity": primary.get("name"),
            "entity_count": entity_count,
            "first_person_count": first_person,
            "ratio": ratio,
        },
        notes=note,
    )
