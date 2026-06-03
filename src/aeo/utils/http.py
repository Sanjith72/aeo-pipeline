"""Shared httpx client factory that forces IPv4 egress.

OCI ARM (Ampere) instances silently drop outbound IPv6 connections, which surfaces
as hung/empty scrapes rather than hard errors. Binding the source socket to the
IPv4 any-address (``local_address="0.0.0.0"``) forces ``AF_INET`` resolution so every
request leaves over IPv4. This is the v4 "force IPv4" infra requirement (see
aeo_architecture_v4.md §"Infra: OCI ARM, US region, force IPv4").

All async HTTP in the pipeline should be constructed via :func:`async_client` so the
IPv4 binding is applied uniformly.
"""
from __future__ import annotations

import httpx

# Source bind address forcing IPv4 (AF_INET). 0.0.0.0 = any local IPv4 interface.
_IPV4_LOCAL_ADDRESS = "0.0.0.0"


def ipv4_transport(retries: int = 0) -> httpx.AsyncHTTPTransport:
    """An async transport bound to IPv4 for OCI ARM compatibility."""
    return httpx.AsyncHTTPTransport(local_address=_IPV4_LOCAL_ADDRESS, retries=retries)


def async_client(**kwargs: object) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` with explicit IPv4 binding.

    Accepts the same keyword arguments as ``httpx.AsyncClient`` (``timeout``,
    ``headers``, ...). A caller may pass its own ``transport`` to override the
    IPv4-bound default.
    """
    kwargs.setdefault("transport", ipv4_transport())
    return httpx.AsyncClient(**kwargs)  # type: ignore[arg-type]
