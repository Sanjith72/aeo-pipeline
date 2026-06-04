"""
The rubric — loaded once from config/scoring.yaml.

This is the single source of truth for *what* the 8 criteria are and *how*
each maps a raw signal to a 1-5 tier. The scorers read their thresholds from
``Criterion.cfg`` so tuning the rubric never requires a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..settings import load_yaml_file


@dataclass(slots=True)
class Criterion:
    name: str
    label: str
    weight: float
    cfg: dict[str, Any]


@dataclass(slots=True)
class Rubric:
    criteria: dict[str, Criterion]
    scale_min: int
    scale_max: int

    def get(self, name: str) -> Criterion:
        return self.criteria[name]

    def names(self) -> list[str]:
        return list(self.criteria.keys())


@lru_cache(maxsize=1)
def load_rubric() -> Rubric:
    raw = load_yaml_file("scoring.yaml")
    scale = raw.get("scale", {}) or {}
    crit_raw = raw.get("criteria", {}) or {}

    criteria: dict[str, Criterion] = {}
    for name, cfg in crit_raw.items():
        criteria[name] = Criterion(
            name=name,
            label=cfg.get("label", name),
            weight=float(cfg.get("weight", 1.0)),
            cfg=cfg or {},
        )

    return Rubric(
        criteria=criteria,
        scale_min=int(scale.get("min", 1)),
        scale_max=int(scale.get("max", 5)),
    )
