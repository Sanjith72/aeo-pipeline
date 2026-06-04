"""
Validation block — re-score the proposed fix on a synthetic page, retry <=3.

Public surface:
  * :func:`validate_page` — the recommend -> simulate -> re-score -> retry loop.
  * :class:`ValidationOutcome` — its result (status, before/after, review routing).
  * :func:`apply_recommendation` — the synthetic-page simulator (also unit-tested
    on its own).
"""

from __future__ import annotations

from .independent import (
    CitationVerdict,
    IndependentCheck,
    IndependentVerdict,
    derive_question,
    validate_independent,
)
from .simulate import apply_recommendation
from .validator import (
    REVIEW_NEEDED,
    REVIEW_NONE,
    STATUS_COULD_NOT_IMPROVE,
    STATUS_IMPROVED,
    STATUS_NO_ACTION,
    ValidationOutcome,
    validate_page,
)

__all__ = [
    "REVIEW_NEEDED",
    "REVIEW_NONE",
    "STATUS_COULD_NOT_IMPROVE",
    "STATUS_IMPROVED",
    "STATUS_NO_ACTION",
    "CitationVerdict",
    "IndependentCheck",
    "IndependentVerdict",
    "ValidationOutcome",
    "apply_recommendation",
    "derive_question",
    "validate_independent",
    "validate_page",
]
