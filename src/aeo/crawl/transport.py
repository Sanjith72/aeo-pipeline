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

import asyncio
import ipaddress

import httpx

from ..settings import get_settings

_IPV4_LOCAL_ADDRESS = "0.0.0.0"

# Address classes an outbound crawl must never reach — the SSRF blocklist. Mirrors the
# entry-point guard in api.app._assert_crawlable_host.
_BLOCKED_IP_ATTRS = (
    "is_private", "is_loopback", "is_link_local",
    "is_reserved", "is_multicast", "is_unspecified",
)


class SSRFError(Exception):
    """Raised when a crawl target resolves to a non-public address (or won't resolve)."""


def ip_is_blocked(ip: str) -> bool:
    """True when ``ip`` is private/loopback/link-local/reserved/multicast/unspecified —
    i.e. must not be reachable from a crawl of an untrusted URL."""
    addr = ipaddress.ip_address(ip)
    return any(getattr(addr, attr) for attr in _BLOCKED_IP_ATTRS)


class _SSRFGuardedAsyncTransport(httpx.AsyncHTTPTransport):
    """An async transport that validates every request's host resolves to a PUBLIC
    address before connecting. Applied only to fetches of UNTRUSTED, user-supplied URLs
    (site discovery + the intake/overview prefill) — never our own API clients
    (LLM/PageSpeed/Perplexity), which may legitimately target localhost.

    Because httpx re-invokes the transport once per hop when following redirects, this
    re-validates every 3xx ``Location`` too — closing the "public host 302s to
    169.254.169.254 / an internal admin port" vector. Resolution runs on the loop's async
    resolver so it never blocks the event loop. (Residual: this validates then lets
    httpcore re-resolve at connect, so a sub-second DNS-rebinding race is not fully
    closed — pinning the vetted IP through the connection would be the belt-and-braces
    follow-up; the redirect vector, the practical exploit, is closed.)"""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, request.url.port or None)
        except OSError as exc:
            raise SSRFError(f"host does not resolve: {host}") from exc
        for info in infos:
            if ip_is_blocked(info[4][0]):
                raise SSRFError(f"blocked non-public address for host: {host}")
        return await super().handle_async_request(request)


def guarded_async_transport() -> httpx.AsyncHTTPTransport:
    """An SSRF-validating async transport (see :class:`_SSRFGuardedAsyncTransport`) for
    fetching untrusted URLs, composed with the force-IPv4 policy. Unlike
    :func:`async_transport` this is never ``None`` — the guard must always be present."""
    kwargs = {"local_address": _IPV4_LOCAL_ADDRESS} if force_ipv4_enabled() else {}
    return _SSRFGuardedAsyncTransport(**kwargs)


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
