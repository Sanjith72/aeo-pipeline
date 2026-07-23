"""v5 CH-02b entitlements — the pack unlock model. Pure logic in :mod:`.logic`; the
data layer is :mod:`aeo.storage.repos.entitlements`."""

from .logic import decorate_pack, is_pack_locked, resolve_unlock_state

__all__ = ["decorate_pack", "is_pack_locked", "resolve_unlock_state"]
