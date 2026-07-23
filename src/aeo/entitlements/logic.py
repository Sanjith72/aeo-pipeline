"""
Pure pack-unlock logic (v5 CH-02b) — no DB, no auth, trivially testable.

The two gates in the spec combine as **OR, with entitlements authoritative**:

  1. **Pack 1 is always free** (implicit — anonymous users have zero entitlement rows and
     still see Pack 1).
  2. **``all_packs`` unlocks everything and bypasses progression** — the agency/advanced
     override exists precisely to skip the earn-forward work.
  3. **A ``pack`` grant for that ``pack_index`` unlocks it regardless of completion** —
     you paid, you're in.
  4. **Free users earn forward** — pack N unlocks once pack N-1 is completed.

Entitlements are paid shortcuts; progression is the free path. ``completed_pack_indices``
is a swappable input: in P3 it is empty (progression inert until the P5 ticket-completion
signal exists), so an anonymous overview shows Pack 1 unlocked and every deeper pack
locked — the derivation changes in P5, this resolver never does.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Only these entitlement scopes affect pack locking; free_overview/tickets are ignored here.
_PACK_SCOPE = "pack"
_ALL_PACKS_SCOPE = "all_packs"


def is_pack_locked(
    pack_index: int,
    *,
    unlocked_pack_indices: Iterable[int] = (),
    all_packs: bool = False,
    completed_pack_indices: Iterable[int] = (),
    progressive: bool = True,
) -> bool:
    """Whether a pack is locked for a viewer with the given grants + progress."""
    unlocked_set = set(unlocked_pack_indices)
    completed_set = set(completed_pack_indices)
    unlocked = (
        pack_index == 1
        or all_packs
        or pack_index in unlocked_set
        or (progressive and (pack_index - 1) in completed_set)
    )
    return not unlocked


def resolve_unlock_state(rows: Iterable[dict[str, Any]]) -> tuple[bool, set[int]]:
    """Reduce entitlement rows to ``(all_packs_flag, {unlocked pack_index})``. Ignores
    every scope except ``pack`` and ``all_packs``; an empty/None-user list → ``(False, set())``
    (Pack-1-only). Callers pass only currently-valid (non-expired) rows."""
    all_packs = False
    unlocked: set[int] = set()
    for row in rows or ():
        scope = row.get("scope")
        if scope == _ALL_PACKS_SCOPE:
            all_packs = True
        elif scope == _PACK_SCOPE and row.get("pack_index") is not None:
            unlocked.add(int(row["pack_index"]))
    return all_packs, unlocked


def decorate_pack(
    pack: dict[str, Any],
    *,
    grants: Iterable[dict[str, Any]],
    completed: Iterable[int] = (),
) -> dict[str, Any]:
    """Return a copy of a pack dict with ``status`` (from the DB column, or 'preview' for
    an unpersisted overview pack) and the entitlement-derived ``locked`` flag. Never
    mutates the input (Pack instances are shared, slotted dataclasses). Both the free
    overview and the pack API route through this, so their lock derivation cannot drift."""
    all_packs, unlocked = resolve_unlock_state(grants)
    out = dict(pack)
    out["status"] = pack.get("status", "preview")
    out["locked"] = is_pack_locked(
        int(pack["pack_index"]),
        unlocked_pack_indices=unlocked,
        all_packs=all_packs,
        completed_pack_indices=completed,
    )
    return out
