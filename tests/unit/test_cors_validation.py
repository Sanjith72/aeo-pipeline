"""Startup validation for AEO__API__CORS_ORIGINS (Phase 6 item 6).

The gap: DEPLOY.md listed this variable as REQUIRED with "must list your deployed web origin",
and `grep -i cors src/aeo/startup.py` returned nothing — no fatal, no warning, at any severity.

Both halves of that were wrong, and the doc half was the more misleading one. The browser never
calls this API: every /api/* request goes through the Next.js server-side proxy, and a
server-to-server request sends no Origin header and is not subject to CORS. So the deployed web
origin does not belong in this list, and a blank value (middleware not installed at all) is a
perfectly correct posture. Validation that nagged about those would be noise.

What is worth catching is a value that is CONFIGURED and cannot work: a browser sends
`Origin: scheme://host[:port]`, exactly, so an entry with a trailing slash or a path matches
nothing and fails as an opaque browser-side CORS error that names no variable.
"""

from __future__ import annotations

import pytest

from aeo.startup import _check_cors


def warns(raw: str) -> list[str]:
    out: list[str] = []
    _check_cors(raw, out)
    return out


# ── the postures that must stay quiet ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "http://localhost:3000,http://127.0.0.1:3000",  # the shipped default
        "https://aeo-studio-nine.vercel.app",
        "https://app.example.com:8443",
        "http://localhost:3000, https://app.example.com",  # whitespace around entries
    ],
)
def test_workable_values_produce_no_warning(raw):
    """Including blank and localhost-only: a proxy-only deployment does not use CORS at all,
    and a warning an operator cannot act on teaches them to ignore the log."""
    assert warns(raw) == [], raw


# ── the values that silently cannot match ──────────────────────────────────────────


def test_a_trailing_slash_can_never_match_an_origin_header():
    w = warns("https://app.example.com/")
    assert len(w) == 1
    assert "never match" in w[0]
    assert "app.example.com" in w[0], "the message must name the offending entry"


def test_a_path_can_never_match_an_origin_header():
    w = warns("https://app.example.com/studio")
    assert len(w) == 1 and "never match" in w[0]


def test_a_bare_host_is_not_an_origin():
    """`example.com` with no scheme is the most natural thing to type and never works."""
    w = warns("app.example.com")
    assert len(w) == 1
    assert "absolute origin" in w[0]


def test_a_wildcard_is_called_out_but_not_treated_as_broken():
    w = warns("*")
    assert len(w) == 1 and "'*'" in w[0]
    assert "never match" not in w[0], "a wildcard works; it is a posture question, not a typo"


def test_each_bad_entry_is_reported_separately():
    w = warns("https://good.example.com,https://bad.example.com/,nope.example.com")
    assert len(w) == 2, w
    assert any("bad.example.com" in m for m in w)
    assert any("nope.example.com" in m for m in w)


def test_nothing_here_is_ever_fatal():
    """CORS is not load-bearing in this topology — a bad entry must not stop the API booting,
    or a cosmetic typo takes production down."""
    fatal: list[str] = []
    warnings: list[str] = []
    from aeo.settings import get_settings

    _check_cors("https://broken.example.com/,*,not-a-url", warnings)
    assert warnings and fatal == []
    assert get_settings is not None  # the module imports cleanly alongside real settings
