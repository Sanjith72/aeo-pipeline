"""
PageSpeed Insights client tests — focused on the credential-handling contract.

The PSI API key must never reach the request URL or a log sink. These tests drive
the real ``fetch`` coroutine with a fake httpx client (no network) and assert (1)
the key travels in the ``x-goog-api-key`` header, never the query string, and (2)
on any non-2xx response the warning carries only the status + exception type, never
``str(exc)`` (which stringifies the full URL and would leak a query-string secret).
"""

from __future__ import annotations

import asyncio

import httpx

from aeo.extract import pagespeed

SECRET = "PSI_SECRET_KEY_abcdef0123456789"


class _RecordingLog:
    """Captures structured log calls so we can assert on what was handed to it."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.warnings.append((event, kw))

    # the module only calls .warning here; stub the rest defensively
    def info(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...
    def debug(self, *a, **k) -> None: ...


class _FakeSettings:
    psi_api_key = SECRET


def _install_fakes(monkeypatch, *, on_get):
    """Wire fake settings + a fake httpx.AsyncClient whose .get runs ``on_get``."""
    monkeypatch.setattr(pagespeed, "get_settings", lambda: _FakeSettings())

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            return await on_get(url, params or {}, headers or {})

    monkeypatch.setattr(pagespeed.httpx, "AsyncClient", _FakeClient)


def test_key_sent_via_header_not_query_string(monkeypatch):
    seen: dict = {}

    async def on_get(url, params, headers):
        seen["params"] = params
        seen["headers"] = headers

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"lighthouseResult": {"categories": {"performance": {"score": 0.9}}, "audits": {}}}

        return _Resp()

    _install_fakes(monkeypatch, on_get=on_get)
    out = asyncio.run(pagespeed.fetch("https://example.com/page"))

    assert out is not None and out["performance_score"] == 90
    # the secret rides in the header, and is absent from the query params (→ the URL)
    assert seen["headers"].get("x-goog-api-key") == SECRET
    assert "key" not in seen["params"]
    assert SECRET not in str(seen["params"])


def test_http_error_does_not_leak_key_in_logs(monkeypatch):
    rec = _RecordingLog()
    monkeypatch.setattr(pagespeed, "log", rec)

    # Worst case: an exception whose str() embeds the secret (as a real
    # HTTPStatusError would embed the request URL). The fix must not log str(exc).
    leaky_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?key={SECRET}"
    req = httpx.Request("GET", leaky_url)
    resp = httpx.Response(429, request=req)
    leaky = httpx.HTTPStatusError(f"Client error '429 Too Many Requests' for url '{leaky_url}'",
                                  request=req, response=resp)

    async def on_get(url, params, headers):
        class _Resp:
            def raise_for_status(self):
                raise leaky

            def json(self):
                return {}

        return _Resp()

    _install_fakes(monkeypatch, on_get=on_get)
    out = asyncio.run(pagespeed.fetch("https://example.com/page"))

    assert out is None
    assert len(rec.warnings) == 1
    event, kw = rec.warnings[0]
    assert event == "psi_fetch_failed"
    assert kw["status"] == 429                 # status surfaced for diagnosis
    assert kw["error"] == "HTTPStatusError"    # only the exception type, never str(exc)
    # the secret must not appear in ANY logged field
    assert SECRET not in " ".join(str(v) for v in kw.values())


def test_transport_error_logs_no_status_and_no_key(monkeypatch):
    rec = _RecordingLog()
    monkeypatch.setattr(pagespeed, "log", rec)

    async def on_get(url, params, headers):
        # Transport-level failure (no response attached → status is None).
        raise httpx.ConnectTimeout("timed out", request=httpx.Request("GET", pagespeed._ENDPOINT))

    _install_fakes(monkeypatch, on_get=on_get)
    out = asyncio.run(pagespeed.fetch("https://example.com/page"))

    assert out is None
    event, kw = rec.warnings[0]
    assert event == "psi_fetch_failed"
    assert kw["status"] is None
    assert kw["error"] == "ConnectTimeout"
    assert SECRET not in " ".join(str(v) for v in kw.values())
