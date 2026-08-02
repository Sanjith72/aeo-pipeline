"""
Stripe Checkout for flat per-pack unlocks (v5 CH-02b).

Resolves §9.2's open pricing decision as **flat price per pack**: each locked pack is a
one-off purchase that grants exactly ``scope='pack', pack_index=N``. No new tables — it
reuses the ``entitlements`` model from migration ``0030``, so payment is only a new *source*
of the grant the unlock resolver already understands (``source='stripe'``).

Deliberately dependency-free: the Stripe SDK is a large transitive tree for two calls, so
this speaks the REST API over the ``httpx`` we already ship and verifies webhook signatures
with ``hmac``. Both are fully exercisable offline, which is why the tests need no network
and no Stripe account.

**The two security invariants** (both load-bearing — a bug in either lets a user unlock a
pack they did not buy):

1. The Checkout Session's ``metadata`` is written SERVER-SIDE from the verified JWT. The
   webhook grants to ``metadata.user_id``, never to anything the browser supplied, so a
   caller cannot buy a pack into a stranger's account (or their own from someone else's
   payment).
2. The webhook body is authenticated by Stripe's HMAC signature BEFORE it is parsed, with a
   constant-time compare and a timestamp tolerance (replay window). An unsigned or stale
   POST never reaches the grant path — this endpoint is exempt from the service X-API-Key
   guard (Stripe cannot send it), so the signature IS its only credential.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from ..logging import get_logger
from ..settings import get_settings

log = get_logger(__name__)

_API_BASE = "https://api.stripe.com/v1"
# Stripe's own recommended replay window. A correctly-clocked server sees events within
# seconds; 5 minutes tolerates clock skew without leaving a long replay window open.
_SIGNATURE_TOLERANCE_SEC = 300


class PaymentsError(RuntimeError):
    """Checkout could not be started (misconfigured or Stripe rejected the request)."""


def payments_enabled() -> bool:
    """True only when BOTH the secret key and the webhook secret are configured.

    Requiring both is a money-safety rule, not tidiness: with a key but no webhook secret,
    checkout works and customers are charged, while every ``checkout.session.completed``
    delivery fails signature verification and 400s. Stripe retries for ~3 days and gives up
    — real money captured, zero entitlements written, and nothing visibly broken except a
    log line. Better to refuse to sell than to sell something we cannot deliver.

    False → the buy path 503s and the UI hides it; promo codes and manual grants still work,
    so the product degrades to the §9.2 stub rather than breaking."""
    cfg = get_settings().payments
    return bool(cfg.enabled and cfg.stripe_secret_key and cfg.webhook_secret)


def _form(payload: dict[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Flatten a nested dict into Stripe's bracketed form encoding
    (``line_items[0][price_data][unit_amount]``). Stripe's API is form-encoded, not JSON."""
    out: list[tuple[str, str]] = []
    for key, value in payload.items():
        field = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.extend(_form(value, field))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(_form(item, f"{field}[{i}]"))
                else:
                    out.append((f"{field}[{i}]", str(item)))
        elif value is not None:
            out.append((field, str(value)))
    return out


def create_pack_checkout(
    *, user_id: str, email: str | None, domain: str, pack_index: int, origin: str
) -> dict[str, Any]:
    """Create a one-time Checkout Session for one pack and return ``{id, url}``.

    ``user_id`` comes from the verified JWT at the call site and is stamped into metadata —
    that stamp is what the webhook later grants against (invariant 1 above)."""
    cfg = get_settings().payments
    if not payments_enabled():
        raise PaymentsError("payments are not configured")

    import httpx

    # The browser-facing origin, NOT the request's. The Next proxy rewrites Host before
    # forwarding, so `origin` here is the backend (http://api:8000 / the API host) and a
    # success_url built from it sends the paying customer to a 404. Only fall back to it
    # when no public URL is configured (single-origin local dev).
    base = (cfg.public_app_url or origin).rstrip("/")
    success = f"{base}{cfg.success_path}"
    cancel = f"{base}{cfg.cancel_path}"
    # Metadata must be echoed on BOTH the session and the payment intent: some Stripe
    # account configurations deliver only one of the two event shapes.
    metadata = {"user_id": user_id, "domain": domain, "pack_index": str(pack_index)}

    line_item: dict[str, Any]
    if cfg.stripe_price_id:
        # A dashboard-managed Price (handles tax behaviour / multi-currency for you).
        line_item = {"price": cfg.stripe_price_id, "quantity": 1}
    else:
        # Inline price — works with a bare Stripe account, no product setup required.
        line_item = {
            "quantity": 1,
            "price_data": {
                "currency": cfg.currency,
                "unit_amount": cfg.pack_price_cents,
                "product_data": {"name": f"{domain} — Pack {pack_index}"},
            },
        }

    payload: dict[str, Any] = {
        "mode": "payment",
        "success_url": success,
        "cancel_url": cancel,
        "client_reference_id": user_id,
        "line_items": [line_item],
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata},
    }
    if email:
        payload["customer_email"] = email

    try:
        res = httpx.post(
            f"{_API_BASE}/checkout/sessions",
            data=_form(payload),
            auth=(cfg.stripe_secret_key or "", ""),
            timeout=cfg.request_timeout_sec,
        )
    except Exception as exc:
        raise PaymentsError(f"could not reach Stripe: {exc}") from exc

    if res.status_code >= 400:
        # Stripe's message is safe to log but NOT to echo verbatim to the browser.
        log.warning("stripe_checkout_failed", status=res.status_code, body=res.text[:500])
        raise PaymentsError("Stripe rejected the checkout request")

    body = res.json()
    return {"id": body.get("id"), "url": body.get("url")}


def verify_webhook(payload: bytes, signature_header: str | None, *, now: float | None = None) -> dict[str, Any]:
    """Verify Stripe's ``Stripe-Signature`` and return the parsed event.

    Raises ValueError on ANY failure — bad/missing header, unknown scheme, stale timestamp,
    or signature mismatch. The body is parsed only after the HMAC check passes, so malformed
    JSON from an unsigned source never reaches the parser."""
    cfg = get_settings().payments
    secret = cfg.webhook_secret
    if not secret:
        raise ValueError("webhook secret is not configured")
    if not signature_header:
        raise ValueError("missing Stripe-Signature header")

    timestamp: str | None = None
    signatures: list[str] = []
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":  # v0 is Stripe's test-mode scheme; only v1 is accepted
            signatures.append(value)
    if not timestamp or not signatures:
        raise ValueError("malformed Stripe-Signature header")

    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise ValueError("malformed timestamp in Stripe-Signature") from exc
    current = time.time() if now is None else now
    if abs(current - sent_at) > _SIGNATURE_TOLERANCE_SEC:
        raise ValueError("Stripe-Signature timestamp outside the tolerance window")

    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    # compare_digest against EVERY provided v1 — Stripe sends several during a secret roll.
    # It raises TypeError on non-ASCII str inputs, so screen candidates first: this endpoint
    # is reachable unauthenticated, and an uncaught TypeError would 500 it (and, because the
    # handler maps only ValueError to 400, surface as a server error Stripe keeps retrying).
    if not any(
        candidate.isascii() and hmac.compare_digest(expected, candidate) for candidate in signatures
    ):
        raise ValueError("Stripe-Signature does not match")

    return json.loads(payload.decode("utf-8"))


#: Event types that can turn into a grant.
#:
#: ``checkout.session.completed`` covers cards, which settle instantly and arrive already
#: 'paid'. DELAYED payment methods (ACH, SEPA, Bacs, some wallets) do NOT: their `completed`
#: arrives 'unpaid'/'processing' and the money lands later on
#: ``checkout.session.async_payment_succeeded``. Subscribing to only the first would mean a
#: customer pays by bank debit and never receives the pack — a silent failure, since nothing
#: errors. Both carry the same session object, so one handler serves both.
GRANTING_EVENTS = frozenset(
    {"checkout.session.completed", "checkout.session.async_payment_succeeded"}
)


def grant_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a verified Stripe event: on a paid checkout, grant that pack.

    Returns the entitlement row, or None when the event is not an unlock (a different type,
    an unpaid session, or metadata we did not write). Safe to call on a REPLAY — the
    underlying grant upserts on ``(user_id, domain, scope, pack_index)``, so a redelivered
    webhook re-grants the same row instead of duplicating or double-charging. That also
    makes the two granting events safely overlapping: a delayed payment fires `completed`
    (ignored, unpaid) then `async_payment_succeeded` (grants), and a card that somehow
    delivered both would simply re-grant the same row."""
    from ..storage.repos import entitlements as entitlements_repo

    if event.get("type") not in GRANTING_EVENTS:
        return None
    session = (event.get("data") or {}).get("object") or {}
    # Stripe marks async payment methods 'unpaid' at completion — only grant on real money.
    if session.get("payment_status") != "paid":
        log.info("stripe_session_not_paid", status=session.get("payment_status"))
        return None

    meta = session.get("metadata") or {}
    user_id, domain, raw_index = meta.get("user_id"), meta.get("domain"), meta.get("pack_index")
    if not user_id or not domain or raw_index is None:
        log.warning("stripe_event_missing_metadata", session_id=session.get("id"))
        return None
    try:
        pack_index = int(raw_index)
    except (TypeError, ValueError):
        log.warning("stripe_event_bad_pack_index", value=raw_index)
        return None

    row = entitlements_repo.grant(
        str(user_id), str(domain), scope="pack", pack_index=pack_index, source="stripe"
    )
    log.info("stripe_pack_granted", domain=domain, pack_index=pack_index)
    return row
