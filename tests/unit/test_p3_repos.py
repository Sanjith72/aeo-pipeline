"""v5 P3 repos — offline API-surface + pure-normalization checks (no Postgres, per the
offline test convention). DB round-trips are verified separately against a live PG."""

from __future__ import annotations

import pytest


def test_packs_repo_exposes_its_api() -> None:
    from aeo.storage.repos import packs

    for fn in ("put_for_run", "by_run", "by_domain"):
        assert callable(getattr(packs, fn))


def test_p4_repo_helpers_exist() -> None:
    # v5 P4 gating helpers: run→domain resolution + the gated pack detail.
    from aeo.storage.repos import runs, skill_scores

    assert callable(runs.domain_for_run)
    assert callable(skill_scores.detail_for_pack)


def test_entitlements_repo_exposes_its_api() -> None:
    from aeo.storage.repos import entitlements

    for fn in ("grant", "list_for_user_domain", "has_access", "ensure_user"):
        assert callable(getattr(entitlements, fn))


def test_grant_rejects_unknown_scope() -> None:
    from aeo.storage.repos import entitlements

    with pytest.raises(ValueError, match="invalid scope"):
        entitlements.grant("u", "example.com", scope="bogus")


def test_grant_pack_scope_requires_pack_index() -> None:
    from aeo.storage.repos import entitlements

    with pytest.raises(ValueError, match="requires a pack_index"):
        entitlements.grant("u", "example.com", scope="pack")


def test_has_access_pack_scope_requires_pack_index() -> None:
    from aeo.storage.repos import entitlements

    with pytest.raises(ValueError, match="requires a pack_index"):
        entitlements.has_access("u", "example.com", "pack")
