"""URL helpers — normalization, host extraction, same-site checks."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse


def normalize(url: str) -> str:
    """Lowercase scheme + host, strip default ports, drop fragment, normalize trailing slash."""
    p = urlparse(url.strip())
    scheme = p.scheme.lower() or "https"
    host = (p.hostname or "").lower()
    if p.port and not _is_default_port(scheme, p.port):
        host = f"{host}:{p.port}"
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, host, path, "", p.query, ""))


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def same_site(a: str, b: str) -> bool:
    return _registrable(host_of(a)) == _registrable(host_of(b))


def absolute(base: str, href: str) -> str:
    return urljoin(base, href)


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme == "http" and port == 80) or (scheme == "https" and port == 443)


def _registrable(host: str) -> str:
    """Cheap eTLD+1 — accurate for common 2-label TLDs (.com, .io, .net…)."""
    parts = host.split(".")
    if len(parts) < 2:
        return host
    return ".".join(parts[-2:])
