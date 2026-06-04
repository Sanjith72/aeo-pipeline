"""
feedback repo — offline guards (no DB).

The status-validation path raises before any connection is opened, so it is unit
-testable: ``criteria_refinements.status`` has a DB CHECK constraint, and the repo
rejects out-of-set values with a clear ValueError rather than letting a raw Postgres
CHECK violation surface at write time.
"""

from __future__ import annotations

import pytest

from aeo.reference.feedback import CriteriaRefinement
from aeo.storage.repos import feedback as feedback_repo
from aeo.storage.repos.feedback import _check_status


def test_check_status_accepts_allowed_values():
    for s in ("proposed", "accepted", "rejected"):
        assert _check_status(s) == s


def test_check_status_rejects_unknown_value():
    with pytest.raises(ValueError):
        _check_status("approved")  # close but not in the allowed set


def test_set_refinement_status_rejects_invalid_before_db():
    # Raises on validation, never reaching the transaction → safe without a DB.
    with pytest.raises(ValueError):
        feedback_repo.set_refinement_status(1, "bogus")


def test_save_refinement_rejects_invalid_status_before_db():
    ref = CriteriaRefinement(
        criterion="stats_in_html", current_target=4, proposed_target=5,
        rationale="x", evidence={}, status="approved",  # invalid
    )
    with pytest.raises(ValueError):
        feedback_repo.save_refinement(ref)
