"""Phase 1 — the gaps the money path still had (v5 CH-02b).

test_payments.py already covers webhook signature verification, the grant shape, and the
async/unpaid event matrix. This file covers what it did not, all of it money-visible:

  * a REPLAYED delivery must be idempotent — Stripe retries, and a retry inside the
    5-minute signature window is a perfectly valid request;
  * ``run_id`` must ride through Stripe's metadata AND the success_url, or a buyer who
    returns in a different tab cannot be put back on their run;
  * the checkout route must REFUSE to sell when no return URL is configured, rather than
    mint a session that lands the buyer on a 404 after a real charge;
  * ``GET /api/config`` must tell the browser what this instance can do — the absence of
    which is why UnlockModal offered a Buy button that 503'd;
  * promo codes must match case/whitespace-insensitively, and the server log must
    distinguish "no codes configured here" from "that code is wrong".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")

SECRET = "whsec_test_secret_value_for_hmac_checks"


def _auth_off(monkeypatch) -> None:
    """Neutralise the deployment credentials so these tests exercise the PAYMENT path, not
    the JWT gate or the service-key gate. Blanking (not delenv) is required: pydantic-settings
    reads the repo .env, so an unset process var still picks up the file's value."""
    for key in ("AEO__AUTH__JWT_SECRET", "AEO__AUTH__JWKS_URL", "AEO__AUTH__JWT_ISSUER",
                "AEO__API__AUTH_KEY"):
        monkeypatch.setenv(key, "")


@pytest.fixture()
def payments_on(monkeypatch):
    monkeypatch.setenv("AEO__PAYMENTS__STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("AEO__PAYMENTS__WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("AEO__PAYMENTS__PUBLIC_APP_URL", "https://app.example.com")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.payments import stripe as mod

    yield mod
    get_settings.cache_clear()


def _event(**meta) -> bytes:
    payload = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_1", "payment_status": "paid",
            "metadata": {"user_id": "u-1", "domain": "x.com", "pack_index": "2", **meta},
        }},
    }
    return json.dumps(payload).encode()


def _sign(body: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    t = int(time.time()) if ts is None else ts
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


class _StripeRes:
    status_code = 200

    @staticmethod
    def json():
        return {"id": "cs_1", "url": "https://checkout.stripe.com/x"}


def _capture_stripe(monkeypatch) -> dict:
    """Intercept the outbound Stripe call and return the flattened form fields it sent."""
    sent: dict = {}
    import httpx

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: (sent.update(dict(kw.get("data") or [])), _StripeRes)[1]
    )
    return sent


# ── webhook: replay safety ────────────────────────────────────────────────────────


def test_a_replayed_event_is_idempotent_not_a_double_grant(payments_on, monkeypatch):
    """Stripe retries deliveries, and a retry inside the signature window is valid — so the
    handler must be safe to run twice. It is, because grant() upserts on
    (user_id, domain, scope, pack_index). This pins that contract so a future change to the
    grant key cannot quietly turn a retry into a second entitlement."""
    calls = []
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: calls.append((a, k)) or {"id": 1})
    evt = json.loads(_event())
    assert payments_on.grant_from_event(evt) is not None
    assert payments_on.grant_from_event(evt) is not None  # byte-identical replay
    assert len(calls) == 2, "both deliveries are handled"
    assert calls[0] == calls[1], "and both write the SAME row — the upsert dedups"


def test_the_grant_is_derived_only_from_verified_metadata(payments_on, monkeypatch):
    """The buyer is whatever the SESSION metadata says, which the server stamped from the
    JWT at checkout time — never anything a browser could have supplied later."""
    calls = []
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "grant", lambda *a, **k: calls.append((a, k)) or {"id": 1})
    payments_on.grant_from_event(json.loads(_event()))
    args, kwargs = calls[0]
    assert args == ("u-1", "x.com")
    assert kwargs["scope"] == "pack"
    assert kwargs["pack_index"] == 2
    assert kwargs["source"] == "stripe"


# Signature verification itself — forged, tampered, stale, unsigned, rotated — is already
# covered by test_payments.py and deliberately not duplicated here.


# ── checkout session: carrying the buyer back ─────────────────────────────────────


def test_run_id_rides_through_metadata_and_the_success_url(payments_on, monkeypatch):
    """So a buyer returning in a DIFFERENT tab or device can still be put back on their run
    — a hosted-checkout round trip cannot rely on this browser's storage."""
    sent = _capture_stripe(monkeypatch)
    from aeo.payments.stripe import create_pack_checkout

    create_pack_checkout(user_id="u-1", email=None, domain="x.com", pack_index=3,
                         origin="http://api:8000", run_id=77)
    assert "run_id=77" in sent["success_url"]
    assert "pack=3" in sent["success_url"]
    assert sent["metadata[run_id]"] == "77"
    # The buyer still comes from the JWT, never from anything the browser can influence.
    assert sent["metadata[user_id]"] == "u-1"


def test_success_url_stays_valid_without_a_run_id(payments_on, monkeypatch):
    sent = _capture_stripe(monkeypatch)
    from aeo.payments.stripe import create_pack_checkout

    create_pack_checkout(user_id="u-1", email=None, domain="x.com", pack_index=2,
                         origin="http://api:8000")
    assert "run_id" not in sent["success_url"]
    assert "checkout=success" in sent["success_url"]
    assert "pack=2" in sent["success_url"]


def test_success_url_uses_the_public_origin_not_the_request(payments_on, monkeypatch):
    """The Next proxy rewrites Host, so request.base_url is the BACKEND's origin. Building
    the redirect from it sent the paying customer to an API 404."""
    sent = _capture_stripe(monkeypatch)
    from aeo.payments.stripe import create_pack_checkout

    create_pack_checkout(user_id="u-1", email=None, domain="x.com", pack_index=2,
                         origin="http://api:8000")
    assert sent["success_url"].startswith("https://app.example.com/")
    assert sent["cancel_url"].startswith("https://app.example.com/")


def test_checkout_503s_when_the_return_url_is_unset(monkeypatch):
    """Defence in depth behind the fatal startup check: refuse to SELL rather than mint a
    session whose success_url returns the buyer to the API host's /studio — a 404 after a
    real charge, with the pack silently granted by the webhook."""
    from fastapi.testclient import TestClient

    _auth_off(monkeypatch)
    monkeypatch.setenv("AEO__PAYMENTS__STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("AEO__PAYMENTS__WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("AEO__PAYMENTS__PUBLIC_APP_URL", "")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import app as app_mod
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    res = TestClient(app_mod.app).post("/api/checkout/pack",
                                       json={"domain": "x.com", "pack_index": 2})
    assert res.status_code == 503
    get_settings.cache_clear()


# ── GET /api/config — the capability endpoint (item 1.2) ──────────────────────────
# UnlockModal decided whether to show "Buy" from packIndex > 1 alone, while comments in
# settings.py and payments/stripe.py both claimed "the UI hides it" when payments are
# unconfigured. That behaviour did not exist: the browser had no way to know.


def _config(monkeypatch, **env) -> dict:
    from fastapi.testclient import TestClient

    _auth_off(monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import app as app_mod

    res = TestClient(app_mod.app).get("/api/config")
    assert res.status_code == 200
    get_settings.cache_clear()
    return res.json()


@pytest.mark.parametrize(
    ("key", "webhook", "url"),
    [
        ("", SECRET, "https://app.example.com"),            # no secret key
        ("sk_test_123", "", "https://app.example.com"),      # no webhook secret
        ("sk_test_123", SECRET, ""),                         # nowhere to return the buyer
    ],
)
def test_config_reports_payments_off_when_any_piece_is_missing(monkeypatch, key, webhook, url):
    """All three are required to complete a sale. Either credential alone takes money and
    grants nothing; a missing return URL takes money and shows a 404."""
    body = _config(monkeypatch, AEO__PAYMENTS__STRIPE_SECRET_KEY=key,
                   AEO__PAYMENTS__WEBHOOK_SECRET=webhook, AEO__PAYMENTS__PUBLIC_APP_URL=url)
    assert body["payments_enabled"] is False


def test_config_reports_payments_on_when_fully_configured(monkeypatch):
    body = _config(monkeypatch, AEO__PAYMENTS__STRIPE_SECRET_KEY="sk_test_123",
                   AEO__PAYMENTS__WEBHOOK_SECRET=SECRET,
                   AEO__PAYMENTS__PUBLIC_APP_URL="https://app.example.com")
    assert body["payments_enabled"] is True


def test_config_reports_promo_availability(monkeypatch):
    assert _config(monkeypatch, AEO__AUTH__PROMO_CODES="")["promo_enabled"] is False
    assert _config(monkeypatch, AEO__AUTH__PROMO_CODES="LAUNCH")["promo_enabled"] is True


def test_config_leaks_no_secrets_lengths_or_counts(monkeypatch):
    """Booleans only. A key LENGTH is a real hint for an offline attack, and a code COUNT
    tells a prober how many guesses are worth making."""
    body = _config(monkeypatch, AEO__PAYMENTS__STRIPE_SECRET_KEY="sk_test_abcdef123456",
                   AEO__PAYMENTS__WEBHOOK_SECRET=SECRET,
                   AEO__PAYMENTS__PUBLIC_APP_URL="https://app.example.com",
                   AEO__AUTH__PROMO_CODES="LAUNCH,SECOND,THIRD")
    assert set(body) == {"payments_enabled", "promo_enabled", "auth_enabled"}
    assert all(isinstance(v, bool) for v in body.values())
    blob = json.dumps(body)
    for leak in ("sk_test", SECRET, "LAUNCH", "app.example.com"):
        assert leak not in blob, leak


# ── promo codes (item 1.3) ────────────────────────────────────────────────────────


def test_promo_codes_match_case_and_whitespace_insensitively(monkeypatch):
    """Codes are retyped off an email, a slide or a badge, and phone keyboards
    autocapitalise. An exact compare made `save20` a different code from `SAVE20`, and the
    user was told the code was invalid or expired — wording that blames a code which is
    perfectly fine."""
    monkeypatch.setenv("AEO__AUTH__PROMO_CODES", " SAVE20 , launch ")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    codes = get_settings().auth.promo_code_set
    assert codes == {"save20", "launch"}
    for submitted in ("SAVE20", "save20", " Save20 ", "sAvE20"):
        assert submitted.strip().casefold() in codes, submitted
    get_settings.cache_clear()


def test_promo_rejection_distinguishes_unconfigured_from_wrong_in_the_log(monkeypatch):
    """The user-facing message stays generic — never confirm to a prober which codes exist.
    But "no codes are configured on this instance" and "that code is wrong" need completely
    different fixes (an env var on the deployed backend vs a typo) and were indistinguishable
    from BOTH sides, which is most of why promo unlocks looked broken."""
    from fastapi.testclient import TestClient

    seen: list[tuple] = []

    class _Log:
        def warning(self, event, **kw):
            seen.append((event, kw))

        def __getattr__(self, _name):
            return lambda *a, **k: None

    import aeo.logging as logging_mod

    monkeypatch.setattr(logging_mod, "get_logger", lambda *a, **k: _Log())
    _auth_off(monkeypatch)
    from aeo.storage.repos import entitlements as ent

    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    from aeo.api import app as app_mod
    from aeo.settings import get_settings

    client = TestClient(app_mod.app)

    monkeypatch.setenv("AEO__AUTH__PROMO_CODES", "")
    get_settings.cache_clear()
    res = client.post("/api/entitlements/redeem", json={"domain": "x.com", "code": "LAUNCH"})
    assert res.status_code == 422
    assert res.json()["detail"] == "invalid or expired promo code"  # generic, deliberately
    assert seen, "the rejection must reach the log"
    assert seen[-1][1]["reason"] == "no_codes_configured"

    monkeypatch.setenv("AEO__AUTH__PROMO_CODES", "REALCODE")
    get_settings.cache_clear()
    res = client.post("/api/entitlements/redeem", json={"domain": "x.com", "code": "WRONG"})
    assert res.status_code == 422
    assert seen[-1][1]["reason"] == "unknown_code"
    # The submitted code is a bearer credential — never logged.
    assert "WRONG" not in json.dumps(seen[-1][1])
    get_settings.cache_clear()


def test_a_correct_promo_code_in_any_casing_unlocks(monkeypatch):
    """End to end through the route: the grant is written for the verified user against the
    NORMALISED domain, which is the same key domain_for_run() resolves to — so the pack list
    actually reflects the unlock."""
    from fastapi.testclient import TestClient

    _auth_off(monkeypatch)
    monkeypatch.setenv("AEO__AUTH__PROMO_CODES", "SAVE20")
    from aeo.settings import get_settings

    get_settings.cache_clear()
    from aeo.api import app as app_mod
    from aeo.storage.repos import entitlements as ent

    granted: list = []
    monkeypatch.setattr(ent, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(ent, "grant", lambda *a, **k: granted.append((a, k)) or {"id": 1})

    res = TestClient(app_mod.app).post(
        "/api/entitlements/redeem",
        json={"domain": "https://WWW.Example.com/pricing", "code": " save20 "},
    )
    assert res.status_code == 200, res.text
    assert res.json()["unlocked"] is True
    args, kwargs = granted[0]
    assert args[1] == "example.com", "the grant must be keyed on the normalised domain"
    assert kwargs["scope"] == "all_packs"
    assert kwargs["source"] == "promo"
    get_settings.cache_clear()
