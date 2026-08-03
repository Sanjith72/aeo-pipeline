"""v5 CH-07 — Supabase-JWT verification. The load-bearing security matrix: the public
anon key and the service_role key are valid same-secret JWTs, so signature verification
alone is a full bypass — only the aud/role/sub checks stop them."""

from __future__ import annotations

import json
import time
import uuid

import pytest

pytest.importorskip("fastapi")  # auth.py imports fastapi
pytest.importorskip("jwt")

import jwt

SECRET = "test-secret-long-enough-for-hs256-32b!!"


def _isolate_from_dotenv(monkeypatch) -> None:
    """Blank every auth key so a developer's real .env FILE can't change the outcome.
    delenv is not enough — pydantic-settings reads .env, so an unset process var still
    picks up the file's value. Setting "" wins over the file and reads as unconfigured."""
    for key in ("AEO__AUTH__JWT_SECRET", "AEO__AUTH__JWKS_URL", "AEO__AUTH__JWT_ISSUER"):
        monkeypatch.setenv(key, "")


@pytest.fixture
def auth_on(monkeypatch):
    """Activate HS256 verification with a known secret; stub the app_users upsert (no DB)."""
    _isolate_from_dotenv(monkeypatch)
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
    _isolate_from_dotenv(monkeypatch)  # neither secret NOR jwks_url -> auth inactive
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


# ── asymmetric / JWKS verification ────────────────────────────────────────────────
# Supabase projects created with JWT signing keys (the current default) have NO shared
# HS256 secret, so the secret-only path rejects every real login. These cover the ES256
# path AND the key-confusion hole it must not open.

JWKS_URL = "https://proj.supabase.co/auth/v1/.well-known/jwks.json"


def _es256_keypair():
    """(private_pem, PyJWK-shaped public JWK) for an ES256 signer."""
    crypto = pytest.importorskip("cryptography")  # noqa: F841 — pyjwt[crypto] extra
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    from jwt.algorithms import ECAlgorithm

    exported = ECAlgorithm.to_jwk(priv.public_key())
    jwk = json.loads(exported) if isinstance(exported, str) else dict(exported)
    jwk.update({"kid": "test-kid", "use": "sig", "alg": "ES256"})
    return pem, jwk


@pytest.fixture
def jwks_on(monkeypatch):
    """Activate ES256/JWKS verification against an in-memory key set (no network)."""
    pem, jwk = _es256_keypair()
    _isolate_from_dotenv(monkeypatch)  # no shared secret — the asymmetric-only case
    monkeypatch.setenv("AEO__AUTH__JWKS_URL", JWKS_URL)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import auth as auth_mod
    from aeo.storage.repos import entitlements as ent

    auth_mod._SEEN_USERS.clear()
    auth_mod._jwk_client.cache_clear()
    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)

    # Serve the key set from memory — PyJWKClient would otherwise hit the network.
    import jwt as jwt_mod

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_signing_key_from_jwt(self, token):
            header = jwt_mod.get_unverified_header(token)
            if header.get("kid") != jwk["kid"]:
                # PyJWT's real wording — auth.py tells a genuine kid miss apart from a fetch
                # failure by it, so an invented message here would hide that logic.
                raise jwt_mod.PyJWKClientError(
                    f'Unable to find a signing key that matches: "{header.get("kid")}"'
                )
            return jwt_mod.PyJWK(jwk)

    monkeypatch.setattr(jwt_mod, "PyJWKClient", _FakeClient)
    yield auth_mod, pem
    auth_mod._jwk_client.cache_clear()
    get_settings.cache_clear()


def _es_tok(pem, **claims) -> str:
    base = {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
            "exp": int(time.time()) + 3600}
    base.update(claims)
    return jwt.encode(base, pem, algorithm="ES256", headers={"kid": "test-kid"})


def test_jwks_url_alone_activates_auth(jwks_on):
    auth_mod, _ = jwks_on
    # An asymmetric project has no secret to set — auth must still be ACTIVE, or the whole
    # gate silently degrades to open.
    assert auth_mod.auth_active() is True


def test_es256_token_accepted_via_jwks(jwks_on):
    auth_mod, pem = jwks_on
    u = auth_mod.get_current_user(_req(_es_tok(pem, email="g@example.com")))
    assert u.role == "authenticated" and u.email == "g@example.com"


def test_es256_forged_key_rejected(jwks_on):
    auth_mod, _ = jwks_on
    other_pem, _ = _es256_keypair()  # correct kid, wrong private key
    with pytest.raises(Exception) as e:
        auth_mod.get_current_user(_req(_es_tok(other_pem)))
    assert _status(e.value) == 401


def test_es256_unknown_kid_rejected(jwks_on):
    auth_mod, pem = jwks_on
    tok = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
         "exp": int(time.time()) + 3600},
        pem, algorithm="ES256", headers={"kid": "not-in-the-key-set"},
    )
    with pytest.raises(Exception) as e:
        auth_mod.get_current_user(_req(tok))
    assert _status(e.value) == 401


def test_hs256_not_accepted_when_only_jwks_configured(jwks_on):
    """No shared secret is configured, so an HS256 token has nothing to verify against —
    it must 401 rather than fall through to some other key."""
    auth_mod, _ = jwks_on
    with pytest.raises(Exception) as e:
        auth_mod.get_current_user(_req(_tok()))
    assert _status(e.value) == 401


def test_blank_env_values_read_as_unset(monkeypatch):
    """`AEO__AUTH__JWT_ISSUER=` in a .env means "not configured" to a human. Before the
    normalizer it reached jwt.decode(issuer="") and rejected EVERY token with a misleading
    'invalid token' 401 — the nastiest kind of misconfiguration, since the deployment looks
    configured. Blank secret / JWKS URL must likewise read as unset, not as a real value."""
    from aeo.settings import get_settings

    for key in ("AEO__AUTH__JWT_SECRET", "AEO__AUTH__JWKS_URL", "AEO__AUTH__JWT_ISSUER"):
        monkeypatch.setenv(key, "   ")
    get_settings.cache_clear()
    cfg = get_settings().auth
    assert (cfg.jwt_secret, cfg.jwks_url, cfg.jwt_issuer) == (None, None, None)
    get_settings.cache_clear()


def test_blank_issuer_does_not_break_a_valid_token(monkeypatch):
    """The end-to-end version of the bug above: a blank issuer must not 401 a good token."""
    _isolate_from_dotenv(monkeypatch)
    monkeypatch.setenv("AEO__AUTH__JWT_SECRET", SECRET)
    monkeypatch.setenv("AEO__AUTH__JWT_ISSUER", "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import auth as auth_mod
    from aeo.storage.repos import entitlements as ent

    auth_mod._SEEN_USERS.clear()
    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    assert auth_mod.get_current_user(_req(_tok())).role == "authenticated"
    get_settings.cache_clear()


def test_asymmetric_algs_rejected_without_jwks(auth_on):
    """The key-confusion guard: on a SECRET-only deployment, ES256/RS256 must not be
    accepted at all. Otherwise an attacker signs with a key of their choosing and the
    verifier treats the public key as the HMAC secret."""
    assert auth_on._algorithms(auth_on._cfg()) == ["HS256"]
    pem, _ = _es256_keypair()
    with pytest.raises(Exception) as e:
        auth_on.get_current_user(_req(_es_tok(pem)))
    assert _status(e.value) == 401


# ── the negative-kid cache must never confuse "couldn't fetch" with "no such key" ──────
#
# _KID_MISS exists to stop a flood of random `kid`s turning every request into an outbound
# JWKS fetch. It works by refusing a remembered kid WITHOUT touching the network — which
# makes what gets remembered safety-critical: record a transport failure as a miss and the
# project's REAL signing key is blacklisted for the whole TTL, converting a momentary
# Supabase blip into a total login outage that cannot self-heal (every request is refused
# instantly, so nothing ever retries the fetch that would fix it).


@pytest.fixture
def jwks_flaky(monkeypatch):
    """Like `jwks_on`, but the fake client can be switched between a transport outage and a
    genuine unknown kid — the two cases auth.py has to tell apart."""
    pem, jwk = _es256_keypair()
    _isolate_from_dotenv(monkeypatch)
    monkeypatch.setenv("AEO__AUTH__JWKS_URL", JWKS_URL)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import auth as auth_mod
    from aeo.storage.repos import entitlements as ent

    auth_mod._SEEN_USERS.clear()
    auth_mod._jwk_client.cache_clear()
    auth_mod._KID_MISS.clear()
    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)

    import jwt as jwt_mod

    state = {"outage": False, "lookups": 0}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def get_signing_key_from_jwt(self, token):
            state["lookups"] += 1
            if state["outage"]:
                # PyJWT 2.8's wording when the key set cannot be RETRIEVED. (2.9+ raises
                # PyJWKClientConnectionError, a subclass — covered by the same handler.)
                raise jwt_mod.PyJWKClientError(
                    'Fail to fetch data from the url, err: "<urlopen error timed out>"'
                )
            header = jwt_mod.get_unverified_header(token)
            if header.get("kid") != jwk["kid"]:
                raise jwt_mod.PyJWKClientError(
                    f'Unable to find a signing key that matches: "{header.get("kid")}"'
                )
            return jwt_mod.PyJWK(jwk)

    monkeypatch.setattr(jwt_mod, "PyJWKClient", _FakeClient)
    yield auth_mod, pem, state
    auth_mod._KID_MISS.clear()
    auth_mod._jwk_client.cache_clear()
    get_settings.cache_clear()


def test_transient_jwks_outage_does_not_blacklist_the_real_kid(jwks_flaky):
    """A Supabase blip must cost only the requests it actually hits.

    Regression: `except Exception: _remember_kid_miss(kid)` recorded a *fetch* failure as
    "this kid does not exist", blacklisting the project's real signing key for the full
    5-minute TTL. Every login in that window then 401'd instantly — including long after
    Supabase recovered, because the short-circuit skips the fetch that would have healed it.
    """
    auth_mod, pem, state = jwks_flaky

    state["outage"] = True
    with pytest.raises(Exception) as e:
        auth_mod.get_current_user(_req(_es_tok(pem)))
    assert _status(e.value) == 401  # the request that lands during the outage does fail

    assert "test-kid" not in auth_mod._KID_MISS, (
        "a transport failure was recorded as an unknown kid — the project's real signing "
        "key is now blacklisted and every login 401s until the TTL expires"
    )

    # Supabase recovers. The very next login must succeed, with no waiting out a TTL.
    state["outage"] = False
    assert auth_mod.get_current_user(_req(_es_tok(pem, email="back@example.com"))).email == (
        "back@example.com"
    )


def test_genuinely_unknown_kid_is_still_remembered(jwks_flaky):
    """The other half: narrowing the handler must not disarm the DoS mitigation. A kid the
    key set actually disowned is still cached, and the repeat is answered from memory."""
    auth_mod, pem, state = jwks_flaky
    tok = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "role": "authenticated",
         "exp": int(time.time()) + 3600},
        pem, algorithm="ES256", headers={"kid": "attacker-supplied"},
    )
    with pytest.raises(Exception) as e:
        auth_mod.get_current_user(_req(tok))
    assert _status(e.value) == 401
    assert "attacker-supplied" in auth_mod._KID_MISS

    lookups = state["lookups"]
    with pytest.raises(Exception) as e:
        auth_mod.get_current_user(_req(tok))
    assert _status(e.value) == 401
    assert state["lookups"] == lookups, (
        "the repeat reached the JWKS client — the negative cache is no longer "
        "short-circuiting, so random kids become unbounded outbound fetches"
    )
