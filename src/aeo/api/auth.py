"""
Supabase-JWT user authentication (v5 CH-07).

A stateless HS256 verifier — the backend never calls Supabase; GoTrue only has to have
*issued* the token, and we verify it with the shared project JWT secret. That is why this
works even when the app data DB is Neon.

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

import uuid
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request

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
    """True when user-JWT verification is configured. When False, auth degrades to
    open/dev (optional → None, required → dev user)."""
    cfg = _cfg()
    return cfg.enabled and bool(cfg.jwt_secret)


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
    is impossible; the secret is the verify key and comes from env only."""
    return jwt.decode(
        token,
        cfg.jwt_secret,
        algorithms=cfg.jwt_algorithms,
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
        raise HTTPException(
            status_code=401, detail="invalid token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    try:
        user = _user_from_claims(claims)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="not an authenticated end-user",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    _ensure_user(user, request)
    return user
