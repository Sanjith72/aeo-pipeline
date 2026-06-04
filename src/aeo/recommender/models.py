"""
Recommendation value type shared by the three generators.

A :class:`Recommendation` is one proposed edit aimed at a rubric criterion. The
generators (schema/content/entity) produce these; the orchestrator persists them
through the recommendations repo and the Validation loop re-scores them. Only
``rec_type`` and ``criterion`` are dedicated columns — everything else (title,
rationale, the concrete edit, provenance) is folded into the ``payload`` JSONB by
:meth:`to_payload`, so the schema never changes when a generator adds a field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# rec_type values (match the recommendations.rec_type column vocabulary)
SCHEMA = "schema"
CONTENT = "content"
ENTITY = "entity"


@dataclass(slots=True)
class Recommendation:
    rec_type: str               # 'schema' | 'content' | 'entity'
    criterion: str | None       # rubric criterion this addresses (None = cross-cutting)
    title: str                  # short human-readable summary
    rationale: str              # why — grounded in the gap / Reference Layer
    payload: dict[str, Any] = field(default_factory=dict)  # the concrete edit
    scored_by: str = "deterministic"  # 'deterministic' | model name

    def to_payload(self) -> dict[str, Any]:
        """Flatten into the JSONB stored in recommendations.payload. Keeps the
        edit body plus the metadata a report/validation step needs."""
        return {
            "title": self.title,
            "rationale": self.rationale,
            "source": self.scored_by,
            **self.payload,
        }
