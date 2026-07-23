"""v5 CH-02b — the pure pack-unlock resolver (truth table)."""

from __future__ import annotations

from aeo.entitlements.logic import decorate_pack, is_pack_locked, resolve_unlock_state


def test_pack_1_always_unlocked_even_anonymous() -> None:
    assert is_pack_locked(1) is False
    assert is_pack_locked(1, unlocked_pack_indices=(), all_packs=False) is False


def test_deeper_packs_locked_without_grants() -> None:
    assert is_pack_locked(2) is True
    assert is_pack_locked(5) is True


def test_all_packs_unlocks_everything_and_bypasses_progression() -> None:
    # No completed packs, but the agency override unlocks every index.
    assert is_pack_locked(4, all_packs=True, completed_pack_indices=frozenset()) is False


def test_direct_pack_grant_unlocks_that_pack_regardless_of_completion() -> None:
    assert is_pack_locked(3, unlocked_pack_indices={3}) is False
    assert is_pack_locked(2, unlocked_pack_indices={3}) is True  # only the granted one


def test_progressive_earn_forward() -> None:
    # Completing Pack 1 unlocks Pack 2 for a free user; Pack 3 stays locked.
    assert is_pack_locked(2, completed_pack_indices={1}) is False
    assert is_pack_locked(3, completed_pack_indices={1}) is True


def test_progression_can_be_disabled() -> None:
    assert is_pack_locked(2, completed_pack_indices={1}, progressive=False) is True


def test_resolve_unlock_state_ignores_irrelevant_scopes() -> None:
    rows = [
        {"scope": "free_overview", "pack_index": None},
        {"scope": "tickets", "pack_index": None},
        {"scope": "pack", "pack_index": 2},
        {"scope": "pack", "pack_index": 4},
    ]
    all_packs, unlocked = resolve_unlock_state(rows)
    assert all_packs is False
    assert unlocked == {2, 4}


def test_resolve_unlock_state_all_packs() -> None:
    all_packs, unlocked = resolve_unlock_state([{"scope": "all_packs", "pack_index": None}])
    assert all_packs is True and unlocked == set()


def test_resolve_unlock_state_empty() -> None:
    assert resolve_unlock_state([]) == (False, set())
    assert resolve_unlock_state(None) == (False, set())


def test_decorate_pack_anonymous_matches_legacy_behavior() -> None:
    # grants=[] must reproduce the old inline `locked = pack_index > 1`.
    p1 = decorate_pack({"pack_index": 1, "title": "Home"}, grants=[])
    p2 = decorate_pack({"pack_index": 2, "title": "More"}, grants=[])
    assert p1["locked"] is False and p1["status"] == "preview"
    assert p2["locked"] is True


def test_decorate_pack_preserves_db_status_and_does_not_mutate_input() -> None:
    src = {"pack_index": 3, "title": "Deep", "status": "scored"}
    out = decorate_pack(src, grants=[{"scope": "pack", "pack_index": 3}])
    # status carried through; a scored pack with a grant is UNLOCKED (status != lock axis)
    assert out["status"] == "scored" and out["locked"] is False
    # a scored pack WITHOUT a grant stays locked — status never unlocks
    assert decorate_pack(src, grants=[])["locked"] is True
    assert "locked" not in src  # input untouched
