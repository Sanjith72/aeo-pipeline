"""
Outbound HTTP transport policy — force IPv4 when configured.

On OCI Ampere (ARM) the default dual-stack resolver can silently stall scraper
fetches on AAAA records. Binding the httpx client to an IPv4 local address
(``0.0.0.0``) forces ``AF_INET`` so every outbound call uses IPv4. Off by default
(dev and most clouds are fine); flip ``AEO__CRAWLER__FORCE_IPV4=true`` on OCI.

One seam every network client (discovery, PageSpeed, Perplexity, the LLM cloud
backend) routes through, so the policy is set in exactly one place.
"""

from __future__ import annotations

import httpx

from ..settings import get_settings

_IPV4_LOCAL_ADDRESS = "0.0.0.0"


def force_ipv4_enabled() -> bool:
    return get_settings().crawler.force_ipv4


def sync_transport() -> httpx.HTTPTransport | None:
    """Transport for a sync ``httpx.Client`` — IPv4-bound when forced, else None
    (httpx then uses its default transport)."""
    if force_ipv4_enabled():
        return httpx.HTTPTransport(local_address=_IPV4_LOCAL_ADDRESS)
    return None


def async_transport() -> httpx.AsyncHTTPTransport | None:
    """Transport for an async ``httpx.AsyncClient`` — IPv4-bound when forced."""
    if force_ipv4_enabled():
        return httpx.AsyncHTTPTransport(local_address=_IPV4_LOCAL_ADDRESS)
    return None
