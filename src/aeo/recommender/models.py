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


# How-to fallbacks per generator, used when a rec doesn't set its own `how_to`.
_HOW_TO_BY_TYPE = {
    SCHEMA: (
        "Add the generated JSON-LD to the page's <head> (or via your tag manager), "
        "then re-validate with Google's Rich Results Test."
    ),
    CONTENT: "Apply the proposed copy edit to the page body, then re-publish the page.",
    ENTITY: "Update the on-page brand/entity references as described, then re-publish.",
}
_HOW_TO_DEFAULT = "Apply the change described above, then re-publish the page."


@dataclass(slots=True)
class Recommendation:
    rec_type: str               # 'schema' | 'content' | 'entity'
    criterion: str | None       # rubric criterion this addresses (None = cross-cutting)
    title: str                  # short human-readable summary
    rationale: str              # why — grounded in the gap / Reference Layer
    payload: dict[str, Any] = field(default_factory=dict)  # the concrete edit
    scored_by: str = "deterministic"  # 'deterministic' | model name
    # #12 — every recommendation surfaces three plain-language fields so the in-app
    # checklist can show "where you are → what to do → how". A generator may set them
    # explicitly; otherwise they derive from rationale/title/rec_type so EVERY rec
    # carries all three (the contract the report + frontend rely on).
    current_state: str = ""     # what's wrong / missing right now
    action_required: str = ""   # the concrete action to take
    how_to: str = ""            # step-by-step / where to apply it

    def effective_current_state(self) -> str:
        return self.current_state or self.rationale

    def effective_action_required(self) -> str:
        return self.action_required or self.title

    def effective_how_to(self) -> str:
        return self.how_to or _HOW_TO_BY_TYPE.get(self.rec_type, _HOW_TO_DEFAULT)

    def to_payload(self) -> dict[str, Any]:
        """Flatten into the JSONB stored in recommendations.payload. Keeps the
        edit body plus the metadata a report/validation step needs."""
        return {
            "title": self.title,
            "rationale": self.rationale,
            "source": self.scored_by,
            "current_state": self.effective_current_state(),
            "action_required": self.effective_action_required(),
            "how_to": self.effective_how_to(),
            **self.payload,
        }
