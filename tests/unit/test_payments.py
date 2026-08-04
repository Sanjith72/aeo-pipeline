"""v5 CH-02b — Stripe Checkout for flat per-pack unlocks.

The security-critical half is the webhook: it is exempt from the service X-API-Key guard,
so its HMAC signature is the ONLY thing standing between a stranger's POST and a free pack.
These run fully offline — no Stripe account, no network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")

SECRET = "whsec_test_secret_value"


def _auth_off(monkeypatch) -> None:
    """Degrade user auth to dev mode so these tests exercise the PAYMENT path, not the JWT
    gate. Blanking (not delenv) is required: pydantic-settings reads the .env FILE, so a
    developer with real auth configured would otherwise get a 401 before the handler runs.

    AEO__API__AUTH_KEY belongs in this list for exactly the reason the paragraph above
    gives, and was missing: it drives ``require_api_key``, a SEPARATE gate from the JWT one,
    which 401s every request that arrives without an ``X-API-Key`` header. These tests send
    no such header, so on any machine whose .env carries a real service key the five
    checkout tests failed — while passing in CI, where there is no .env at all. A test whose
    result depends on whether an untracked file exists reports nothing about the code."""
    for key in ("AEO__AUTH__JWT_SECRET", "AEO__AUTH__JWKS_URL", "AEO__AUTH__JWT_ISSUER",
                "AEO__API__AUTH_KEY"):
        monkeypatch.setenv(key, "")


@pytest.fixture
def payments_on(monkeypatch):
    """Configure Stripe keys on the live settings object and stub the entitlement write."""
    monkeypatch.setenv("AEO__PAYMENTS__STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("AEO__PAYMENTS__WEBHOOK_SECRET", SECRET)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.payments import stripe as mod

    yield mod
    get_settings.cache_clear()


def _sign(body: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    t = int(time.time()) if ts is None else ts
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _event(**meta) -> bytes:
    payload = {
        "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "payment_status": "paid",
                            "metadata": {"user_id": "u-1", "domain": "x.com", "pack_index": "2", **meta}}},
    }
    return json.dumps(payload).encode()


# ── signature verification ────────────────────────────────────────────────────────


def test_valid_signature_accepted(payments_on):
    body = _event()
    assert payments_on.verify_webhook(body, _sign(body))["type"] == "checkout.session.completed"


def test_forged_signature_rejected(payments_on):
    body = _event()
    with pytest.raises(ValueError):
        payments_on.verify_webhook(body, _sign(body, secret="whsec_attacker"))


def test_missing_header_rejected(payments_on):
    with pytest.raises(ValueError):
        payments_on.verify_webhook(_event(), None)


def test_tampered_body_rejected(payments_on):
    """Sign one body, deliver another — the classic 'change the pack_index in flight'."""
    header = _sign(_event())
    with pytest.raises(ValueError):
        payments_on.verify_webhook(_event(pack_index="99"), header)


def test_stale_timestamp_rejected(payments_on):
    body = _event()
    old = int(time.time()) - 3600
    with pytest.raises(ValueError):
        payments_on.verify_webhook(body, _sign(body, ts=old))


def test_unsigned_garbage_never_reaches_the_json_parser(payments_on):
    """Body is parsed only AFTER the HMAC passes, so malformed JSON from an unsigned
    source raises the signature error, not a JSON error."""
    with pytest.raises(ValueError, match=r"does not match|malformed"):
        payments_on.verify_webhook(b"not json at all", _sign(b"different bytes"))


def test_no_webhook_secret_configured_rejects_everything(monkeypatch):
    monkeypatch.setenv("AEO__PAYMENTS__WEBHOOK_SECRET", "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.payments import stripe as mod

    body = _event()
    with pytest.raises(ValueError):
        mod.verify_webhook(body, _sign(body))
    get_settings.cache_clear()


def test_secret_rotation_accepts_either_signature(payments_on):
    """Stripe sends several v1 signatures while a signing secret is being rolled."""
    body = _event()
    good = _sign(body).split("v1=")[1]
    t = _sign(body).split(",")[0].split("=")[1]
    header = f"t={t},v1=deadbeef,v1={good}"
    assert payments_on.verify_webhook(body, header)["type"] == "checkout.session.completed"


# ── event -> entitlement ──────────────────────────────────────────────────────────


def test_paid_event_grants_that_pack(payments_on, monkeypatch):
    calls = []
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: calls.append((a, k)) or {"id": 1})
    row = payments_on.grant_from_event(json.loads(_event()))
    assert row is not None
    (args, kwargs) = calls[0]
    assert args == ("u-1", "x.com")
    assert kwargs["scope"] == "pack" and kwargs["pack_index"] == 2 and kwargs["source"] == "stripe"


def test_unpaid_session_grants_nothing(payments_on, monkeypatch):
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: pytest.fail("must not grant"))
    evt = json.loads(_event())
    evt["data"]["object"]["payment_status"] = "unpaid"
    assert payments_on.grant_from_event(evt) is None


def test_unrelated_event_type_ignored(payments_on, monkeypatch):
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: pytest.fail("must not grant"))
    evt = json.loads(_event())
    evt["type"] = "invoice.paid"
    assert payments_on.grant_from_event(evt) is None


def test_missing_metadata_grants_nothing(payments_on, monkeypatch):
    """Metadata is what we stamped server-side; without it there is no one to grant to —
    never fall back to anything the payload might otherwise suggest."""
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: pytest.fail("must not grant"))
    evt = json.loads(_event())
    evt["data"]["object"]["metadata"] = {}
    assert payments_on.grant_from_event(evt) is None


def test_non_numeric_pack_index_grants_nothing(payments_on, monkeypatch):
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: pytest.fail("must not grant"))
    assert payments_on.grant_from_event(json.loads(_event(pack_index="; DROP"))) is None


# ── form encoding (Stripe's API is form-encoded, not JSON) ─────────────────────────


def test_nested_form_encoding():
    from aeo.payments.stripe import _form

    out = dict(_form({"line_items": [{"price_data": {"unit_amount": 4900}, "quantity": 1}]}))
    assert out["line_items[0][price_data][unit_amount]"] == "4900"
    assert out["line_items[0][quantity]"] == "1"


def test_form_encoding_drops_none():
    from aeo.payments.stripe import _form

    assert dict(_form({"a": 1, "b": None})) == {"a": "1"}


# ── route wiring ──────────────────────────────────────────────────────────────────


def test_webhook_route_is_exempt_from_the_api_key_guard(monkeypatch):
    """Stripe cannot send X-API-Key. If the guard covered this path every real payment
    would 401 and silently never grant."""
    from unittest.mock import MagicMock

    from aeo.api.app import require_api_key
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().api, "auth_key", "s3cret")
    req = MagicMock()
    req.url.path = "/api/webhooks/stripe"
    req.method = "POST"
    req.headers = {}
    require_api_key(req)  # must not raise

    other = MagicMock()
    other.url.path = "/api/packs/1"
    other.method = "GET"
    other.headers = {}
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:  # the exemption must be path-specific
        require_api_key(other)
    assert exc.value.status_code == 401


def test_payments_disabled_without_a_key(monkeypatch):
    monkeypatch.setenv("AEO__PAYMENTS__STRIPE_SECRET_KEY", "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.payments.stripe import payments_enabled

    assert payments_enabled() is False
    get_settings.cache_clear()


# ── POST /api/checkout/pack ───────────────────────────────────────────────────────


@pytest.fixture
def checkout_client(payments_on, monkeypatch):
    """TestClient with auth degraded to the dev user and Stripe's HTTP call stubbed."""
    from fastapi.testclient import TestClient

    _auth_off(monkeypatch)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import app as app_mod
    from aeo.payments import stripe as pay
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(ent, "list_for_user_domain", lambda uid, d: [])
    monkeypatch.setattr(
        pay, "create_pack_checkout",
        lambda **kw: {"id": "cs_test", "url": "https://checkout.stripe.com/c/pay/cs_test", **kw},
    )
    return TestClient(app_mod.app)


def test_checkout_returns_a_session_url(checkout_client):
    res = checkout_client.post("/api/checkout/pack", json={"domain": "x.com", "pack_index": 2})
    assert res.status_code == 200
    assert res.json()["checkout_url"].startswith("https://checkout.stripe.com/")


def test_pack_1_is_never_sold(checkout_client):
    """Pack 1 is free by the unlock rule — charging for it would be selling nothing."""
    res = checkout_client.post("/api/checkout/pack", json={"domain": "x.com", "pack_index": 1})
    assert res.status_code == 422


def test_already_unlocked_pack_409s_instead_of_charging_twice(checkout_client, monkeypatch):
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "list_for_user_domain", lambda uid, d: [{"scope": "all_packs"}])
    res = checkout_client.post("/api/checkout/pack", json={"domain": "x.com", "pack_index": 3})
    assert res.status_code == 409


def test_buyer_comes_from_the_token_not_the_body(checkout_client, monkeypatch):
    """The session metadata must be stamped from the verified user. If the body could set
    it, anyone could pay a pack into (or out of) another account."""
    seen = {}
    from aeo.payments import stripe as pay

    monkeypatch.setattr(
        pay, "create_pack_checkout",
        lambda **kw: seen.update(kw) or {"id": "cs", "url": "https://checkout.stripe.com/x"},
    )
    checkout_client.post(
        "/api/checkout/pack",
        json={"domain": "x.com", "pack_index": 2, "user_id": "attacker-controlled"},
    )
    assert seen["user_id"] != "attacker-controlled"


def test_checkout_503s_when_payments_unconfigured(monkeypatch):
    from fastapi.testclient import TestClient

    _auth_off(monkeypatch)
    monkeypatch.setenv("AEO__PAYMENTS__STRIPE_SECRET_KEY", "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import app as app_mod
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    res = TestClient(app_mod.app).post("/api/checkout/pack", json={"domain": "x.com", "pack_index": 2})
    assert res.status_code == 503
    get_settings.cache_clear()


def test_delayed_payment_grants_on_the_async_success_event(payments_on, monkeypatch):
    """ACH/SEPA/Bacs: `completed` arrives unpaid and must NOT grant; the money lands later
    on `async_payment_succeeded`, which must. Ignoring the second event would mean a paying
    customer silently never receives the pack."""
    granted = []
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: granted.append(k) or {"id": 1})

    pending = json.loads(_event())
    pending["data"]["object"]["payment_status"] = "unpaid"
    assert payments_on.grant_from_event(pending) is None
    assert granted == []

    settled = json.loads(_event())
    settled["type"] = "checkout.session.async_payment_succeeded"
    assert payments_on.grant_from_event(settled) is not None
    assert granted[0]["pack_index"] == 2


def test_async_payment_failed_grants_nothing(payments_on, monkeypatch):
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: pytest.fail("must not grant"))
    evt = json.loads(_event())
    evt["type"] = "checkout.session.async_payment_failed"
    evt["data"]["object"]["payment_status"] = "unpaid"
    assert payments_on.grant_from_event(evt) is None


# ── admin boundary: the service key is NOT an authorization boundary ───────────────
# web/app/api/[...path]/route.ts injects X-API-Key into every /api/* request it forwards, so
# any visitor's browser can present it. Gating entitlement MINTING on it let a signed-in user
# POST themselves all_packs from the devtools console and walk the whole paywall for free.


def _grant_body():
    return {"user_id": "11111111-1111-1111-1111-111111111111", "domain": "x.com", "scope": "all_packs"}


def test_grant_is_refused_when_only_the_service_key_is_configured(monkeypatch):
    """Deployed posture with no admin credential must FAIL CLOSED, not stay open."""
    from fastapi.testclient import TestClient

    from aeo.api import app as app_mod
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().api, "auth_key", "s3rvice")
    monkeypatch.setattr(get_settings().api, "admin_key", None)
    res = TestClient(app_mod.app).post(
        "/api/entitlements/grant", json=_grant_body(), headers={"X-API-Key": "s3rvice"}
    )
    assert res.status_code == 503, "service key alone must never mint entitlements"


def test_grant_is_refused_when_NEITHER_key_is_configured(monkeypatch):
    """The hole this closes. ``require_admin_key`` used to key its refusal off ``auth_key``,
    so "neither key set" fell straight through and returned None — leaving the
    entitlement-minting route completely ungated for anyone who could reach the backend's
    own URL (the Vercel proxy denylist only covers the proxy). Boot validation now refuses
    that posture outright, but the guard must fail closed on its own: this module is
    importable by uvicorn or a test client with no lifespan, and a boundary enforced only
    when some other check happened to run is not a boundary."""
    from fastapi.testclient import TestClient

    from aeo.api import app as app_mod
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().api, "auth_key", None)
    monkeypatch.setattr(get_settings().api, "admin_key", None)
    # raising=False so this is a BEHAVIOUR diff, not an AttributeError, against a tree
    # without the new flag: on the old guard the request sailed past into the handler.
    monkeypatch.setattr(get_settings().api, "allow_open", False, raising=False)
    res = TestClient(app_mod.app).post("/api/entitlements/grant", json=_grant_body())
    assert res.status_code == 503, "an unconfigured API must not mint entitlements"


def test_grant_stays_open_only_when_open_mode_is_explicitly_named(monkeypatch):
    """The deliberate local-dev carve-out: fully-open dev keeps working, but only for a
    process that was TOLD to be open. Reaching 422 (not 503) proves the guard passed and
    the route body ran — the body then rejects this payload for its own reason, which is
    all we need here and keeps the test off the database."""
    from fastapi.testclient import TestClient

    from aeo.api import app as app_mod
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().api, "auth_key", None)
    monkeypatch.setattr(get_settings().api, "admin_key", None)
    monkeypatch.setattr(get_settings().api, "allow_open", True)
    body = {**_grant_body(), "scope": "pack", "pack_index": None}  # scope='pack' needs an index
    res = TestClient(app_mod.app).post("/api/entitlements/grant", json=body)
    assert res.status_code == 422, "explicit open mode must not break local dev"


def test_grant_requires_the_admin_key_when_configured(monkeypatch):
    from fastapi.testclient import TestClient

    from aeo.api import app as app_mod
    from aeo.settings import get_settings

    monkeypatch.setattr(get_settings().api, "auth_key", "s3rvice")
    monkeypatch.setattr(get_settings().api, "admin_key", "adm1n")
    client = TestClient(app_mod.app)

    # The proxy-injected service key is not enough — this is the exploit request.
    assert client.post(
        "/api/entitlements/grant", json=_grant_body(), headers={"X-API-Key": "s3rvice"}
    ).status_code == 403
    # A wrong admin key is refused too.
    assert client.post(
        "/api/entitlements/grant", json=_grant_body(),
        headers={"X-API-Key": "s3rvice", "X-Admin-Key": "nope"},
    ).status_code == 403


def test_proxy_denylist_blocks_the_grant_path():
    """Second layer: the Next proxy must not forward the admin route at all."""
    import re
    from pathlib import Path

    src = Path("web/app/api/[...path]/route.ts").read_text(encoding="utf-8")
    assert "BLOCKED_PATHS" in src
    assert "entitlements/grant" in src
    # the guard must run BEFORE the target URL is built
    assert re.search(r"BLOCKED_PATHS\.has\([^)]*\)[\s\S]{0,200}?const target", src)


def test_payments_disabled_without_a_webhook_secret(monkeypatch):
    """Selling with no webhook secret takes money and never grants the pack — Stripe's
    deliveries all 400 and it gives up after ~3 days."""
    monkeypatch.setenv("AEO__PAYMENTS__STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("AEO__PAYMENTS__WEBHOOK_SECRET", "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.payments.stripe import payments_enabled

    assert payments_enabled() is False
    get_settings.cache_clear()


def test_success_url_uses_the_public_app_origin_not_the_request(payments_on, monkeypatch):
    """The Next proxy rewrites Host, so request.base_url is the BACKEND. Building the
    redirect from it sent the paying customer to an API 404."""
    sent = {}

    class _Res:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "cs_1", "url": "https://checkout.stripe.com/x"}

    import httpx

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: (sent.update(dict(kw.get("data") or [])), _Res)[1]
    )
    monkeypatch.setenv("AEO__PAYMENTS__PUBLIC_APP_URL", "https://app.example.com")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.payments.stripe import create_pack_checkout

    create_pack_checkout(
        user_id="u-1", email=None, domain="x.com", pack_index=2,
        origin="http://api:8000",  # what request.base_url would give behind the proxy
    )
    assert sent["success_url"].startswith("https://app.example.com/")
    assert sent["cancel_url"].startswith("https://app.example.com/")
    get_settings.cache_clear()


def test_non_ascii_signature_raises_valueerror_not_typeerror(payments_on):
    """The webhook is unauthenticated; a TypeError would 500 it (and the handler maps only
    ValueError to 400, so Stripe would retry a server error for days)."""
    body = _event()
    t = int(time.time())
    with pytest.raises(ValueError):
        payments_on.verify_webhook(body, f"t={t},v1=\u00e9\u00e9\u00e9")


# ── startup validation: refuse to sell what we cannot deliver ──────────────────────


def _validate(monkeypatch, **env):
    from aeo.settings import get_settings
    from aeo.startup import StartupValidationError, validate_settings

    # These tests are about the PAYMENTS half of startup validation. Serving with no
    # AEO__API__AUTH_KEY is independently fatal (an open /api/entitlements/grant), which
    # would mask every payments assertion below with an unrelated error, so declare the
    # localhost posture explicitly. Individual tests override it when the key is the subject.
    monkeypatch.setenv("AEO__API__ALLOW_OPEN", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        return validate_settings(serving=True), None
    except StartupValidationError as exc:
        return None, str(exc)
    finally:
        get_settings.cache_clear()


def test_startup_is_fatal_when_the_webhook_secret_is_missing(monkeypatch):
    """The silent money-loser: checkout works, customers are charged, every webhook is
    rejected, Stripe gives up after ~3 days. Must not boot."""
    _warns, err = _validate(
        monkeypatch,
        AEO__PAYMENTS__STRIPE_SECRET_KEY="sk_test_123",
        AEO__PAYMENTS__WEBHOOK_SECRET="",
    )
    assert err is not None and "WEBHOOK_SECRET" in err


def test_startup_ok_when_both_stripe_credentials_are_set(monkeypatch):
    warns, err = _validate(
        monkeypatch,
        AEO__PAYMENTS__STRIPE_SECRET_KEY="sk_test_123",
        AEO__PAYMENTS__WEBHOOK_SECRET="whsec_123",
        AEO__PAYMENTS__PUBLIC_APP_URL="https://app.example.com",
    )
    assert err is None
    assert not any("WEBHOOK_SECRET" in w or "PUBLIC_APP_URL" in w for w in warns)


def test_startup_warns_when_the_return_url_is_unset(monkeypatch):
    """Without it the buyer is redirected to the API's own origin — a 404 after paying."""
    warns, err = _validate(
        monkeypatch,
        AEO__PAYMENTS__STRIPE_SECRET_KEY="sk_test_123",
        AEO__PAYMENTS__WEBHOOK_SECRET="whsec_123",
        AEO__PAYMENTS__PUBLIC_APP_URL="",
    )
    assert err is None
    assert any("PUBLIC_APP_URL" in w for w in warns)


def test_startup_is_silent_about_payments_when_stripe_is_unconfigured(monkeypatch):
    """No Stripe key = the documented §9.2 stub, not a misconfiguration."""
    warns, err = _validate(monkeypatch, AEO__PAYMENTS__STRIPE_SECRET_KEY="")
    assert err is None
    assert not any("PAYMENTS" in w for w in warns)


def test_startup_flags_a_missing_admin_key_in_a_deployed_posture(monkeypatch):
    from aeo.settings import get_settings

    monkeypatch.setenv("AEO__API__AUTH_KEY", "s3rvice")
    monkeypatch.setenv("AEO__API__ADMIN_KEY", "")
    get_settings.cache_clear()
    from aeo.startup import validate_settings

    warns = validate_settings(serving=True)
    assert any("ADMIN_KEY" in w for w in warns)
    get_settings.cache_clear()


def test_jwks_only_deployment_is_not_reported_as_auth_disabled(monkeypatch):
    """A JWKS-only project (the Supabase default — no shared secret exists) is correctly
    gated; warning that auth is off trains you to ignore the warning that matters."""
    from aeo.settings import get_settings

    monkeypatch.setenv("AEO__AUTH__JWT_SECRET", "")
    monkeypatch.setenv("AEO__AUTH__JWKS_URL", "https://p.supabase.co/auth/v1/.well-known/jwks.json")
    # This test is about the USER-auth warning; the separate service-key check is fatal when
    # serving, and would abort the run before any warning could be collected.
    monkeypatch.setenv("AEO__API__ALLOW_OPEN", "1")
    get_settings.cache_clear()
    from aeo.startup import validate_settings

    warns = validate_settings(serving=True)
    assert not any("deep-value routes" in w for w in warns)
    get_settings.cache_clear()


def test_stripe_webhook_is_exempt_from_the_rate_limiter():
    """Stripe retries in bursts from shared egress IPs; a 429 is a delivery FAILURE, so a
    throttled webhook loses the grant for a payment already taken."""
    from aeo.api.app import _HEALTH_PATH, _STRIPE_WEBHOOK_PATH

    assert _STRIPE_WEBHOOK_PATH == "/api/webhooks/stripe"
    src = __import__("pathlib").Path("src/aeo/api/app.py").read_text(encoding="utf-8")
    assert "exempt = (_HEALTH_PATH, _STRIPE_WEBHOOK_PATH)" in src
    assert "path not in exempt" in src
    assert _HEALTH_PATH == "/api/health"
