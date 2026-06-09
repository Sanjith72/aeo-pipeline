"""
HTTP API (SP-4a) — a thin FastAPI layer over the ``aeo`` package.

The API does not reimplement any logic: each endpoint calls a function that already
exists (``plan_from_brief``, ``generate_blueprint``, ``build_asset_bundle``,
``Orchestrator.dry_run``). It is the integration seam the SP-4 React/Next wizard
(see ``PRODUCT_FLOW.md``) is designed against. Requires the optional ``[api]`` extra
(``pip install -e ".[api]"``).
"""

from __future__ import annotations

from .app import app

__all__ = ["app"]
