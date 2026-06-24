"""
Validation block — re-score the proposed fix on a synthetic page, retry <=3.

Public surface:
  * :func:`validate_page` — the recommend -> simulate -> re-score -> retry loop.
  * :class:`ValidationOutcome` — its result (status, before/after, review routing).
  * :func:`apply_recommendation` — the synthetic-page simulator (also unit-tested
    on its own).
"""

from __future__ import annotations

from .adversarial import (
    AdversarialVerdict,
    CitationSignal,
    adversarial_audit,
    check_citation_signals,
    extract_citation_urls,
)
from .independent import (
    CitationVerdict,
    IndependentCheck,
    IndependentVerdict,
    derive_question,
    validate_independent,
)
from .predict import (
    BASIS_NO_LIFT,
    BASIS_SIMULATED,
    BASIS_UNKNOWN,
    PredictedLift,
    predict_lifts,
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
    "BASIS_NO_LIFT",
    "BASIS_SIMULATED",
    "BASIS_UNKNOWN",
    "REVIEW_NEEDED",
    "REVIEW_NONE",
    "STATUS_COULD_NOT_IMPROVE",
    "STATUS_IMPROVED",
    "STATUS_NO_ACTION",
    "AdversarialVerdict",
    "CitationSignal",
    "CitationVerdict",
    "IndependentCheck",
    "IndependentVerdict",
    "PredictedLift",
    "ValidationOutcome",
    "adversarial_audit",
    "apply_recommendation",
    "check_citation_signals",
    "derive_question",
    "extract_citation_urls",
    "predict_lifts",
    "validate_independent",
    "validate_page",
]
