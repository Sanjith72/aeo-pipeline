"""v5 CH-07 — Supabase-JWT verification. The load-bearing security matrix: the public
anon key and the service_role key are valid same-secret JWTs, so signature verification
alone is a full bypass — only the aud/role/sub checks stop them."""

from __future__ import annotations

import time
import uuid

import pytest

pytest.importorskip("fastapi")  # auth.py imports fastapi
pytest.importorskip("jwt")

import jwt

SECRET = "test-secret-long-enough-for-hs256-32b!!"


@pytest.fixture
def auth_on(monkeypatch):
    """Activate HS256 verification with a known secret; stub the app_users upsert (no DB)."""
    monkeypatch.setenv("AEO__AUTH__JWT_SECRET", SECRET)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import auth as auth_mod
    from aeo.storage.repos import entitlements as ent

    auth_mod._SEEN_USERS.clear()
    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    yield auth_mod
    get_settings.cache_clear()


def _req(token: str | None = None, cookie: str | None = None):
    from unittest.mock import MagicMock

    r = MagicMock()
    r.headers = {"authorization": f"Bearer {token}"} if token else {}
    r.cookies = {"aeo_sid": cookie} if cookie else {}
    return r


def _tok(secret: str = SECRET, alg: str = "HS256", **claims) -> str:
    base = {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
            "exp": int(time.time()) + 3600}
    base.update(claims)
    return jwt.encode(base, secret, algorithm=alg)


def _status(exc) -> int | None:
    return getattr(exc, "status_code", None)


def test_valid_user_token_accepted(auth_on):
    u = auth_on.get_current_user(_req(_tok(email="a@b.com")))
    assert u.role == "authenticated" and u.email == "a@b.com"
    uuid.UUID(u.id)  # sub is a UUID


def test_anon_key_rejected(auth_on):
    # Supabase anon key: valid signature, role=anon → must NOT authenticate as a user.
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_tok(role="anon")))
    assert _status(e.value) == 401


def test_service_role_key_rejected(auth_on):
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_tok(role="service_role")))
    assert _status(e.value) == 401


def test_forged_signature_rejected(auth_on):
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_tok(secret="attacker-secret-also-32-chars-long!!")))
    assert _status(e.value) == 401


def test_alg_none_rejected(auth_on):
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated"},
        None, algorithm="none",
    )
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(forged))
    assert _status(e.value) == 401


def test_wrong_audience_rejected(auth_on):
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_tok(aud="anon")))
    assert _status(e.value) == 401


def test_non_uuid_sub_rejected(auth_on):
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_tok(sub="not-a-uuid")))
    assert _status(e.value) == 401


def test_expired_token_rejected(auth_on):
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_tok(exp=int(time.time()) - 10)))
    assert _status(e.value) == 401


def test_missing_token_is_401(auth_on):
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req())
    assert _status(e.value) == 401


def test_optional_user_never_raises(auth_on):
    assert auth_on.get_optional_user(_req("garbage.token.here")) is None
    assert auth_on.get_optional_user(_req()) is None
    assert auth_on.get_optional_user(_req(_tok(role="anon"))) is None  # bad token → anon
    assert auth_on.get_optional_user(_req(_tok())).role == "authenticated"


def test_disabled_mode_degrades(monkeypatch):
    monkeypatch.delenv("AEO__AUTH__JWT_SECRET", raising=False)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import auth as auth_mod
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    auth_mod._SEEN_USERS.clear()
    assert auth_mod.auth_active() is False
    assert auth_mod.get_optional_user(_req("anything")) is None  # optional stays anonymous
    dev = auth_mod.get_current_user(_req())  # required → deterministic dev user
    assert dev.id == "00000000-0000-0000-0000-000000000000"
    get_settings.cache_clear()
