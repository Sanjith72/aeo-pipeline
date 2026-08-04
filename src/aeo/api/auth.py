"""
Supabase-JWT user authentication (v5 CH-07).

A stateless verifier — the backend never calls Supabase to check a session; GoTrue only has
to have *issued* the token. That is why this works even when the app data DB is Neon.

Two verification modes, chosen by what is configured:

* **Shared secret (HS256)** — ``AEO__AUTH__JWT_SECRET``, the project's legacy JWT secret.
* **Asymmetric (ES256/RS256) via JWKS** — ``AEO__AUTH__JWKS_URL``. Supabase projects created
  with *JWT signing keys* (the current default) have no shared secret at all, so the
  secret-only path would reject every real login. The public keys are fetched + cached from
  the project's JWKS endpoint and selected by the token's ``kid``.

Either alone is enough; both together is a valid rotation window. The asymmetric algorithms
are accepted **only** when a JWKS URL is configured, so a secret-only deployment can never be
tricked into verifying an attacker-supplied public key as an HMAC secret.

**The load-bearing security fact:** Supabase's PUBLIC anon key and the service_role key are
themselves long-lived JWTs signed with the SAME HS256 secret, so signature verification
alone passes for them. What distinguishes a real end-user token is
``aud == "authenticated"`` + ``role == "authenticated"`` + a UUID ``sub``. Dropping any of
those checks turns the public anon key into a universal login. The signature is necessary;
the claim checks are the actual access control.

Two per-route dependencies, composed WITH (never replacing) the global ``require_api_key``
service guard:
  * :func:`get_optional_user` — never raises; ``None`` for anonymous. Keeps the P3 free tier
    byte-identical (anonymous → ``grants=[]`` → Pack 1 only).
  * :func:`get_current_user` — 401 on missing/invalid/expired.

Auth degrades to open/dev when the secret is unset (mirrors ``AEO__API__AUTH_KEY``): the
optional dep returns ``None`` and the required dep returns a deterministic dev user, so
local dev needs no Supabase. ``verify_signature=False`` appears NOWHERE.
"""

from __future__ import annotations

import json
import time
import urllib.error
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import exceptions as jwt_exceptions

from ..logging import get_logger
from ..settings import AuthCfg, get_settings

log = get_logger(__name__)

_BEARER_PREFIX = "bearer "
_MAX_TOKEN_LEN = 8192  # a sane ceiling so a giant header can't waste decode work

# Fire ensure_user at most once per process per user (mirrors the in-memory rate limiter):
# an unguarded per-request upsert is a needless WAL write / minor DoS vector.
_SEEN_USERS: set[str] = set()


@dataclass(frozen=True)
class User:
    id: str            # JWT `sub` (UUID string) → app_users.id
    email: str | None
    role: str | None   # must be "authenticated" for a real end-user token


def _cfg() -> AuthCfg:
    return get_settings().auth


def auth_active() -> bool:
    """True when user-JWT verification is configured — by EITHER a shared secret or a JWKS
    URL. When False, auth degrades to open/dev (optional → None, required → dev user)."""
    cfg = _cfg()
    return cfg.enabled and bool(cfg.jwt_secret or cfg.jwks_url)


def _algorithms(cfg: AuthCfg) -> list[str]:
    """The accepted algorithms for this deployment. Asymmetric algs are added ONLY when a
    JWKS URL is configured — without it there is no public key to verify against, and
    accepting RS256/ES256 alongside an HMAC secret is the classic key-confusion hole."""
    algs = list(cfg.jwt_algorithms)
    if cfg.jwks_url:
        algs += [a for a in cfg.jwt_asymmetric_algorithms if a not in algs]
    return algs


@lru_cache(maxsize=4)
def _jwk_client(url: str, lifespan: int) -> Any:
    """One cached PyJWKClient per URL — it holds the fetched JWKS in memory and refreshes on
    a `kid` miss (so a key rotation heals without a redeploy). Cached because constructing
    one per request would refetch the key set on every call.

    ``timeout`` is pinned short: PyJWKClient defaults to 30s, and this runs in the sync
    dependency threadpool, so one slow fetch can hold a worker for half a minute."""
    from jwt import PyJWKClient

    return PyJWKClient(url, cache_keys=True, lifespan=lifespan, timeout=_JWKS_FETCH_TIMEOUT_SEC)


# Unknown-`kid` negative cache. A miss makes PyJWKClient refetch the whole key set, and the
# failure is NOT cached by its lru_cache — so a stream of tokens bearing random `kid`s turns
# every request into an outbound HTTP fetch on a threadpool worker: an unauthenticated
# DoS against both this API and the JWKS endpoint. We remember recently-rejected kids and
# refuse them without touching the network, while still allowing periodic refetches so a
# genuine key ROTATION still heals on its own.
_KID_MISS: dict[str, float] = {}
_KID_MISS_TTL_SEC = 300
_KID_MISS_MAX = 512
_JWKS_FETCH_TIMEOUT_SEC = 5


def _is_unknown_kid(exc: BaseException) -> bool:
    """Did the key set come back fine and simply not contain this ``kid`` (safe to remember),
    or could the key set not be RETRIEVED at all (must never be remembered)?

    This distinction is load-bearing. ``_remember_kid_miss`` blacklists a ``kid`` for
    ``_KID_MISS_TTL_SEC``, and ``_kid_recently_missed`` then refuses it *without touching the
    network*. So recording a fetch failure as a miss blacklists the REAL, VALID signing key:
    one transient Supabase blip (a timeout, a 502, a DNS hiccup) turns into a full FIVE-MINUTE
    outage in which every genuine login is rejected with no attempt to recover — and because
    each rejection is instant, nothing retries the fetch that would have healed it.

    Defaults to False. An unrecognised error is treated as transient, which costs at most the
    DoS mitigation for that one request; the reverse mistake costs every user their login. The
    actual attack (a flood of random ``kid``s) always produces PyJWT's "Unable to find a
    signing key that matches" — the one case we do want to remember — so the mitigation is
    unaffected.
    """
    # Fetch-side failures: urllib (DNS/TLS/refused/timeout), a read timeout, or a body that
    # is not JSON. None of these say anything about whether the kid exists.
    if isinstance(exc, (urllib.error.URLError, TimeoutError, json.JSONDecodeError)):
        return False
    # PyJWT >= 2.9 gives fetch failures their own class; 2.8 (our floor) does not.
    conn_error = getattr(jwt_exceptions, "PyJWKClientConnectionError", None)
    if conn_error is not None and isinstance(exc, conn_error):
        return False
    if isinstance(exc, jwt_exceptions.PyJWKClientError):
        # Stable across PyJWT 2.4→2.10 for the genuine kid-miss; a fetch failure on 2.8
        # arrives as this type too but with a "Fail to fetch data from the url" message.
        return "unable to find a signing key" in str(exc).lower()
    return False


def _kid_recently_missed(kid: str) -> bool:
    hit = _KID_MISS.get(kid)
    if hit is None:
        return False
    if time.time() - hit >= _KID_MISS_TTL_SEC:
        _KID_MISS.pop(kid, None)
        return False
    return True


def _remember_kid_miss(kid: str) -> None:
    now = time.time()
    if len(_KID_MISS) >= _KID_MISS_MAX:  # drop expired, then oldest — a real memory bound
        for k, ts in list(_KID_MISS.items()):
            if now - ts >= _KID_MISS_TTL_SEC:
                _KID_MISS.pop(k, None)
        if len(_KID_MISS) >= _KID_MISS_MAX:
            for k, _ in sorted(_KID_MISS.items(), key=lambda kv: kv[1])[: len(_KID_MISS) - _KID_MISS_MAX + 1]:
                _KID_MISS.pop(k, None)
    _KID_MISS[kid] = now


def _verify_key(token: str, cfg: AuthCfg) -> Any:
    """The key to verify this token with: the JWKS public key matching its ``kid`` when
    asymmetric verification is configured and the header names one, else the shared secret.
    Raises when neither is available, so an unverifiable token can never fall through."""
    header = jwt.get_unverified_header(token)  # header only — no trust placed in it
    alg = header.get("alg")
    if cfg.jwks_url and alg in cfg.jwt_asymmetric_algorithms:
        kid = header.get("kid")
        if isinstance(kid, str) and _kid_recently_missed(kid):
            # Refuse without touching the network (see _KID_MISS). Same 401 either way.
            raise ValueError("unknown signing key")
        try:
            return _jwk_client(cfg.jwks_url, cfg.jwks_cache_sec).get_signing_key_from_jwt(token).key
        except Exception as exc:
            # ONLY remember a kid the key set actually disowned — never one we simply
            # couldn't look up (see _is_unknown_kid).
            if isinstance(kid, str) and _is_unknown_kid(exc):
                _remember_kid_miss(kid)
            raise
    if cfg.jwt_secret:
        return cfg.jwt_secret
    raise ValueError(f"no verification key configured for alg={alg!r}")


def _token_fingerprint(token: str) -> dict[str, Any]:
    """Non-secret identifying bits of a token, for a log line. The JOSE header is
    unauthenticated metadata that anyone holding the token can already read, and `alg`/`kid`
    are exactly what distinguishes "our JWKS doesn't have this key" from "someone is
    forging". The token, its signature and every claim stay out of the log."""
    try:
        header = jwt.get_unverified_header(token)
        return {"alg": header.get("alg"), "kid": header.get("kid")}
    except Exception:
        return {"alg": None, "kid": None, "header": "unparseable"}


def _extract_bearer(request: Request) -> str | None:
    """The bearer token from the Authorization header, or None. Case-insensitive scheme."""
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith(_BEARER_PREFIX):
        return None
    token = header[len(_BEARER_PREFIX):].strip()
    if not token or len(token) > _MAX_TOKEN_LEN:
        return None
    return token


def _decode(token: str, cfg: AuthCfg) -> dict:
    """Verify signature + aud + exp and decode claims. Raises jwt exceptions on any
    failure. Algorithms are pinned (never omitted) so alg=none / RS256→HS256 confusion
    is impossible; the key is the env secret or a JWKS public key, never anything the
    caller supplies."""
    return jwt.decode(
        token,
        _verify_key(token, cfg),
        algorithms=_algorithms(cfg),
        audience=cfg.jwt_aud,
        issuer=cfg.jwt_issuer,  # None → issuer not checked (only pinned when configured)
        leeway=cfg.leeway_sec,
        options={
            "require": ["exp", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": True,
        },
    )


def _user_from_claims(claims: dict) -> User:
    """Turn verified claims into a User, enforcing the checks that block the anon /
    service_role keys (which are valid same-secret JWTs). Raises ValueError on a
    non-user token so callers map it to 401."""
    if claims.get("role") != "authenticated":
        raise ValueError("token is not an authenticated end-user (role != authenticated)")
    sub = claims.get("sub")
    try:
        uuid.UUID(str(sub))  # app_users.id is UUID — reject a non-UUID sub before any FK write
    except (ValueError, TypeError) as exc:
        raise ValueError("token sub is not a UUID") from exc
    email = claims.get("email")
    return User(id=str(sub), email=email if isinstance(email, str) else None, role="authenticated")


def _ensure_user(user: User, request: Request) -> None:
    """Best-effort: upsert the app_users row on first sight so entitlement FKs hold, and
    claim the pre-auth aeo_sid session (COOKIE-sourced — never a body field, which would
    let a user claim a stranger's session). Never 500s a request over the upsert; fires
    at most once per process per user."""
    if user.id in _SEEN_USERS:
        return
    try:
        from ..storage.repos import entitlements as entitlements_repo

        sid = request.cookies.get("aeo_sid")
        entitlements_repo.ensure_user(user.id, email=user.email, session_id=sid)
        _SEEN_USERS.add(user.id)
    except Exception as exc:  # user provisioning must never break a request
        log.warning("ensure_user_failed", user_id=user.id, error=str(exc))


def _dev_user() -> User:
    return User(id=_cfg().dev_user_id, email=None, role="authenticated")


def get_optional_user(request: Request) -> User | None:
    """The anonymous-friendly dependency: a verified :class:`User`, or ``None`` when auth
    is inactive or no/invalid token is present. NEVER raises — a bad token on an
    optional route degrades to anonymous (Pack 1 only), never a 401."""
    if not auth_active():
        return None
    token = _extract_bearer(request)
    if token is None:
        return None
    try:
        user = _user_from_claims(_decode(token, _cfg()))
    except Exception:  # invalid/expired/non-user token → treat as anonymous
        return None
    _ensure_user(user, request)
    return user


def get_current_user(request: Request) -> User:
    """The gate: a verified :class:`User`, or 401. In disabled mode returns a deterministic
    dev user so pack-detail routes stay reachable in local dev (never trusts an untrusted
    token — no verify_signature=False)."""
    if not auth_active():
        user = _dev_user()
        _ensure_user(user, request)
        return user
    token = _extract_bearer(request)
    if token is None:
        raise HTTPException(
            status_code=401, detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = _decode(token, _cfg())
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=401, detail="token expired",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except Exception as exc:
        # The response body stays deliberately vague (never tell a prober WHY), but the
        # server log must not be. Without this, a misconfigured JWKS URL and a forged token
        # are the same opaque "invalid token" — which is precisely how a deployment can 401
        # every real login with no trace of the cause anywhere. None of these fields are
        # secret: the header is unauthenticated metadata and the token itself is never logged.
        log.warning(
            "jwt_verify_failed",
            error_type=type(exc).__name__,
            error=str(exc),
            **_token_fingerprint(token),
        )
        raise HTTPException(
            status_code=401, detail="invalid token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    try:
        user = _user_from_claims(claims)
    except ValueError as exc:
        # The one 401 branch that still logged nothing. A token can pass every signature and
        # registered-claim check and still be refused here — and this is the branch the
        # project's own anon / service_role keys land in, since they are valid JWTs signed
        # with the same secret. Without a log line, "someone is probing with the public anon
        # key" and "our issuer pin is wrong" were the same silence. The reason string names
        # the failed claim only (role / sub shape); no claim VALUE is logged.
        log.warning(
            "jwt_not_end_user", reason=str(exc), **_token_fingerprint(token),
        )
        raise HTTPException(
            status_code=401, detail="not an authenticated end-user",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    _ensure_user(user, request)
    return user
