"""Auth CONFIGURATION checking (Phase 2 items 2.4 + 2.5) — the false-green class.

test_auth.py covers the verifier itself: given a config, which tokens are accepted. This
file covers the layer above it, where the failures are silent rather than loud. Both known
false greens have the same shape — every environment variable is present, `auth_active()` is
True, `check_auth_config.py` prints "gating is configured", and every real login 401s with a
generic "invalid token" that names nothing:

  * a wrong ``AEO__AUTH__JWT_ISSUER`` (``jwt.decode`` compares it EXACTLY, so one trailing
    slash is a total outage), which nothing validated — ``startup._check_auth`` grew jwks_url
    checks but never looked at the issuer at all; and
  * a project that signs ASYMMETRICALLY while the backend is configured secret-only. The
    live probe only caught the inverse (JWKS URL set, key set empty).

The last test covers 2.5's server-log half: the 401 stays vague to the client, but the
reason must reach the log or this class stays unfalsifiable.
"""

from __future__ import annotations

import importlib.util
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")  # api/auth.py imports fastapi
pytest.importorskip("jwt")

import jwt

SECRET = "test-secret-long-enough-for-hs256-32b!!"
GOOD_URL = "https://klnzsbguvitpnixnvsqs.supabase.co"
GOOD_ISSUER = f"{GOOD_URL}/auth/v1"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_auth_config.py"


def _script():
    """Import scripts/check_auth_config.py as a module. It is a script, not a package, so
    it has no import path — loading it by location is what lets its logic be tested at all
    instead of only run by hand."""
    spec = importlib.util.spec_from_file_location("check_auth_config", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tok(secret: str = SECRET, alg: str = "HS256", headers: dict | None = None, **claims) -> str:
    base = {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
            "exp": int(time.time()) + 3600}
    base.update(claims)
    return jwt.encode(base, secret, algorithm=alg, headers=headers)


@pytest.fixture()
def auth_on(monkeypatch):
    """HS256 verification with a known secret, isolated from the developer's real .env."""
    for key in ("AEO__AUTH__JWT_SECRET", "AEO__AUTH__JWKS_URL", "AEO__AUTH__JWT_ISSUER"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("AEO__AUTH__JWT_SECRET", SECRET)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import auth as auth_mod
    from aeo.storage.repos import entitlements as ent

    auth_mod._SEEN_USERS.clear()
    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    yield auth_mod
    get_settings.cache_clear()


@pytest.fixture()
def settings(monkeypatch):
    """A mutable Settings the startup validator sees instead of the cached one."""
    from aeo import startup as startup_mod
    from aeo.settings import Settings

    s = Settings()
    s.api.allow_open = True  # the service-key check is not the subject of these tests
    monkeypatch.setattr(startup_mod, "get_settings", lambda: s)
    return s


# ── issuer validation: the pure checker (2.4a) ────────────────────────────────────


def test_canonical_issuer_is_accepted() -> None:
    assert _script().issuer_problems(GOOD_ISSUER) == []
    assert _script().issuer_problems(GOOD_ISSUER, GOOD_URL) == []


def test_a_trailing_slash_is_caught() -> None:
    """The invisible one: dashboards and copy-paste add it, the comparison is exact, and
    every login 401s while every variable reads as correct."""
    assert any("trailing slash" in p for p in _script().issuer_problems(GOOD_ISSUER + "/"))


def test_a_wrong_path_is_caught() -> None:
    for bad in (GOOD_URL, f"{GOOD_URL}/auth/v2", f"{GOOD_URL}/auth"):
        assert any("auth/v1" in p for p in _script().issuer_problems(bad)), bad


def test_a_non_absolute_or_non_https_issuer_is_caught() -> None:
    for bad in ("klnzsbguvitpnixnvsqs.supabase.co/auth/v1", "http://x.supabase.co/auth/v1"):
        assert any("https" in p for p in _script().issuer_problems(bad)), bad


def test_a_placeholder_issuer_is_caught() -> None:
    assert any("placeholder" in p for p in
               _script().issuer_problems("https://<project-ref>.supabase.co/auth/v1"))


def test_an_issuer_from_another_project_is_caught() -> None:
    found = _script().issuer_problems("https://otherproj.supabase.co/auth/v1", GOOD_URL)
    assert any("should be exactly" in p for p in found)


def test_an_unpinned_issuer_is_not_a_problem() -> None:
    """The pin is optional; only a WRONG pin breaks logins. Reporting an absent one as an
    error would make the checker cry wolf on a perfectly valid configuration."""
    assert _script().issuer_problems(None) == []
    assert _script().issuer_problems("") == []


# ── issuer validation: the startup half (2.4c) ────────────────────────────────────


@pytest.mark.parametrize(
    ("issuer", "match"),
    [
        (GOOD_ISSUER + "/", "trailing slash"),
        ("klnzsbguvitpnixnvsqs.supabase.co/auth/v1", "absolute https"),
        ("https://<project-ref>.supabase.co/auth/v1", "placeholder"),
    ],
)
def test_startup_is_fatal_on_a_broken_issuer(settings, issuer, match) -> None:
    from aeo.startup import StartupValidationError, validate_settings

    settings.auth.jwt_issuer = issuer
    with pytest.raises(StartupValidationError, match=match):
        validate_settings(serving=True)


def test_startup_warns_on_an_odd_issuer_path(settings) -> None:
    from aeo.startup import validate_settings

    settings.auth.jwt_issuer = f"{GOOD_URL}/auth/v2"
    assert any("auth/v1" in w for w in validate_settings(serving=True))


def test_startup_warns_when_issuer_and_jwks_are_different_projects(settings) -> None:
    """Keys from one project, issuer pin from another: the signature verifies and then the
    issuer check refuses it. Completely silent until a real user tries to log in."""
    from aeo.startup import validate_settings

    settings.auth.jwt_issuer = GOOD_ISSUER
    settings.auth.jwks_url = "https://otherproj.supabase.co/auth/v1/.well-known/jwks.json"
    assert any("same Supabase" in w for w in validate_settings(serving=True))


def test_startup_is_quiet_about_a_correct_issuer(settings) -> None:
    from aeo.startup import validate_settings

    settings.auth.jwt_issuer = GOOD_ISSUER
    settings.auth.jwks_url = f"{GOOD_URL}/auth/v1/.well-known/jwks.json"
    assert not any("ISSUER" in w for w in validate_settings(serving=True))


# ── --token mode (2.5a) ───────────────────────────────────────────────────────────
# Every other probe inspects configuration, so "the backend accepts a REAL user token"
# stayed an assumption — and it is exactly the assumption that fails when JWKS_URL or
# JWT_ISSUER are subtly wrong. Each of these must name WHICH check refused.


def test_a_genuine_token_is_accepted(auth_on) -> None:
    ok, lines = _script().verify_real_token(_tok())
    assert ok, lines
    assert any("WOULD be accepted" in line for line in lines)


def test_an_expired_token_is_named_as_expired_not_invalid(auth_on) -> None:
    ok, lines = _script().verify_real_token(_tok(exp=int(time.time()) - 10))
    assert not ok
    assert any("EXPIRED" in line for line in lines)


def test_an_issuer_mismatch_is_named(auth_on, monkeypatch) -> None:
    """The headline case. From outside this is byte-identical to a forged signature."""
    monkeypatch.setenv("AEO__AUTH__JWT_ISSUER", GOOD_ISSUER)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    ok, lines = _script().verify_real_token(_tok(iss="https://someone-else.supabase.co/auth/v1"))
    assert not ok
    assert any("ISSUER MISMATCH" in line for line in lines)


def test_a_wrong_audience_is_named(auth_on) -> None:
    ok, lines = _script().verify_real_token(_tok(aud="anon"))
    assert not ok
    assert any("AUDIENCE MISMATCH" in line for line in lines)


def test_a_bad_signature_is_named(auth_on) -> None:
    ok, lines = _script().verify_real_token(_tok(secret="a-completely-different-secret!!!"))
    assert not ok
    assert any("SIGNATURE" in line for line in lines)


def test_the_anon_key_is_reported_as_not_an_end_user(auth_on) -> None:
    """The load-bearing security fact: the project's PUBLIC anon key is a valid JWT signed
    with the SAME secret, so signature verification alone passes for it. Only the
    role/aud/sub checks separate it from a real login."""
    ok, lines = _script().verify_real_token(_tok(role="anon"))
    assert not ok
    assert any("NOT an end-user" in line for line in lines)


def test_an_asymmetric_token_with_no_jwks_url_is_named(auth_on) -> None:
    """The false green this item exists for. api/auth._algorithms() offers ES256/RS256 ONLY
    when a JWKS URL is configured, so on a secret-only config nothing can verify these —
    while every environment variable reads as present and correct."""
    # Assembled by hand rather than via jwt.encode: only the HEADER matters here (the check
    # fires before any signature work, because on a secret-only config there is no key that
    # could verify it), and PyJWT refuses to even encode RS256 with an HMAC secret.
    import base64
    import json

    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    token = ".".join([
        b64({"alg": "RS256", "typ": "JWT", "kid": "k1"}),
        b64({"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
             "exp": int(time.time()) + 3600}),
        "not-a-real-signature",
    ])
    ok, lines = _script().verify_real_token(token)
    assert not ok
    assert any("JWKS_URL is not set" in line for line in lines)


def test_garbage_is_reported_without_crashing(auth_on) -> None:
    ok, lines = _script().verify_real_token("not-a-jwt-at-all")
    assert not ok
    assert any("parseable" in line for line in lines)


def test_inactive_auth_is_reported_rather_than_silently_passing(monkeypatch) -> None:
    for key in ("AEO__AUTH__JWT_SECRET", "AEO__AUTH__JWKS_URL"):
        monkeypatch.setenv(key, "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    ok, lines = _script().verify_real_token(_tok())
    assert not ok
    assert any("INACTIVE" in line for line in lines)
    get_settings.cache_clear()


def test_the_output_never_leaks_the_token_the_email_or_the_secret(auth_on) -> None:
    """The output is meant to be pasteable into an issue or a chat."""
    token = _tok(email="someone@private.example")
    ok, lines = _script().verify_real_token(token)
    assert ok
    blob = "\n".join(lines)
    assert token not in blob
    assert "private.example" not in blob
    assert SECRET not in blob


# ── the server-log half (2.5b) ────────────────────────────────────────────────────


def test_the_non_end_user_401_reaches_the_server_log(auth_on, monkeypatch) -> None:
    """The client response stays deliberately vague — never tell a prober why. But this was
    the one 401 branch that logged NOTHING, so "someone is probing with the public anon key"
    and "our issuer pin is wrong" produced identical silence."""
    from fastapi import HTTPException

    seen: list[tuple] = []
    monkeypatch.setattr(auth_on.log, "warning", lambda ev, **kw: seen.append((ev, kw)))

    from unittest.mock import MagicMock

    req = MagicMock()
    req.headers = {"authorization": f"Bearer {_tok(role='anon')}"}
    req.cookies = {}

    with pytest.raises(HTTPException) as exc:
        auth_on.get_current_user(req)
    assert exc.value.status_code == 401
    assert exc.value.detail == "not an authenticated end-user"  # unchanged: still vague
    events = [ev for ev, _ in seen]
    assert "jwt_not_end_user" in events, f"the reason must reach the log; got {events}"


def test_the_log_line_carries_the_reason_but_no_claim_values(auth_on, monkeypatch) -> None:
    from fastapi import HTTPException

    seen: list[tuple] = []
    monkeypatch.setattr(auth_on.log, "warning", lambda ev, **kw: seen.append((ev, kw)))

    from unittest.mock import MagicMock

    token = _tok(role="anon", email="someone@private.example")
    req = MagicMock()
    req.headers = {"authorization": f"Bearer {token}"}
    req.cookies = {}

    with pytest.raises(HTTPException):
        auth_on.get_current_user(req)
    payload = next(kw for ev, kw in seen if ev == "jwt_not_end_user")
    assert "role" in payload["reason"]                 # names the failed check
    blob = repr(payload)
    assert "private.example" not in blob               # never a claim VALUE
    assert token not in blob
