"""
HTTP API (SP-4a) — a thin FastAPI layer over the ``aeo`` package.

The API does not reimplement any logic: each endpoint calls a function that already
exists (``plan_from_brief``, ``generate_blueprint``, ``build_asset_bundle``,
``Orchestrator.dry_run``). It is the integration seam the SP-4 React/Next wizard
(see ``PRODUCT_FLOW.md``) is designed against.

This package init is deliberately empty of imports: the FastAPI app lives at
``aeo.api.app:app`` (what ``aeo serve``/uvicorn load) and needs the optional ``[api]``
extra, while ``aeo.api.jobs`` — the pure-stdlib in-memory job registry — must stay
importable in environments without FastAPI (the offline test suite, worker-only
images). An eager ``from .app import app`` here is what used to break both.
"""

from __future__ import annotations
