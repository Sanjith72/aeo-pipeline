#!/usr/bin/env python
"""Verify the v5 auth/gating config is actually live (CH-07/CH-02a).

Run after filling in .env + web/.env.local:

    python scripts/check_auth_config.py

Reports which verification mode is active and whether the frontend half is configured.
Prints only booleans, lengths and hostnames — NEVER a secret value — so the output is
safe to paste into an issue or a chat.

`--live` additionally probes the Supabase project over the network. This is the half that
kept being missed: every *env var* can be set correctly and Google sign-in still fails,
because the two settings that actually route the OAuth return leg live in the Supabase
DASHBOARD, not in any file this repo owns:

    Authentication → URL Configuration → Site URL
    Authentication → URL Configuration → Redirect URLs

If the deployed origin's `/auth/callback` is not in that allowlist, GoTrue silently
discards `redirect_to` after Google consent and bounces the user to **Site URL** instead —
which defaults to `http://localhost:3000`. Nothing errors, no log line appears, the user
just lands somewhere else and is never signed in on the site they started from. `--live`
turns that invisible failure into a red line:

    python scripts/check_auth_config.py --live --site https://aeo-studio-nine.vercel.app

Everything `--live` sends is read-only — it asks GoTrue to redirect a deliberately INVALID
token and reads only the Location header. No user, session or email is ever created.

`--token` closes the last gap. Every other check here inspects CONFIGURATION, and
configuration can be entirely correct-looking while the backend rejects every genuine
login — because `get_current_user` answers "invalid token" to a forged token, a wrong
issuer, an unmatched `kid` and an expired token alike (deliberately: a prober must learn
nothing). Paste a real access token and this runs it through the SAME code path the API
uses, then names which check refused it:

    python scripts/check_auth_config.py --token -      # reads from stdin, not argv

The token, the email and every secret stay out of the output, so it is safe to paste
into an issue. Copy the token from the browser after signing in on the deployed origin:
devtools -> Application -> Local Storage -> the `sb-<ref>-auth-token` entry -> the
`access_token` field.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
OK, BAD, WARN = "[ok]", "[!!]", "[--]"
HTTP_TIMEOUT = 15


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE from a .env file, ignoring comments/blanks. Not a full dotenv
    parser — enough to tell 'set' from 'not set'."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


# ── live probes (--live) ──────────────────────────────────────────────────────


def _get(url: str, headers: dict[str, str] | None = None, *, follow: bool = True):
    """GET `url`, returning (status, headers, body_bytes). Never raises for an HTTP error
    status — a 4xx is data here, not an exception. When `follow` is False, a 3xx is
    returned as-is so the Location header can be inspected (that header IS the signal)."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None  # returning None is how urllib is told to stop following

    opener = urllib.request.build_opener(*([_NoRedirect()] if not follow else []))
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:  # includes the 3xx we refused to follow
        return e.code, dict(e.headers), e.read()


def _canonical(raw: str) -> str:
    """scheme://host[:port]/path for comparison — fragment and query dropped. GoTrue appends
    its error as a #fragment, so the fragment must not be part of the match."""
    u = urlparse(raw)
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", "", ""))


def _probe_redirect_allowlist(supabase_url: str, anon: str, candidate: str) -> tuple[bool, str]:
    """Is `candidate` in the project's Redirect URLs allowlist?

    GoTrue validates `redirect_to` against the allowlist BEFORE it reports a bad token, so a
    deliberately invalid token is enough to read the routing decision off the Location header:

        allowlisted     → Location is `candidate` itself (plus an #error fragment)
        NOT allowlisted → Location is the project's **Site URL** — which the header reveals

    Returns (allowed, location). Nothing is created or consumed; the token is garbage.
    """
    qs = urlencode({"token": "aeo-config-probe-invalid", "type": "signup", "redirect_to": candidate})
    status, headers, _ = _get(f"{supabase_url}/auth/v1/verify?{qs}", {"apikey": anon}, follow=False)
    location = headers.get("Location") or headers.get("location") or ""
    if not location:
        raise RuntimeError(f"no Location header from GoTrue (status {status}) — cannot judge the allowlist")
    return _canonical(location) == _canonical(candidate), location


def issuer_problems(issuer: str | None, supabase_url: str | None = None) -> list[str]:
    """Everything wrong with an issuer pin that can be judged from strings alone.

    Pure and importable so the unit tests can cover it without a network or a .env. Returns
    [] when ``issuer`` is unset — the pin is optional; it is only a *wrong* pin that is fatal.

    Why this is the highest-value check in the file: ``jwt.decode(issuer=...)`` compares
    exactly, so `/auth/v1/` and `/auth/v1` are different issuers. Every login then 401s with
    "invalid token" — byte-identical to what a forged token produces — and nothing anywhere
    says the word "issuer". Every env var reads as correct while no one can sign in.
    """
    if not issuer:
        return []
    out: list[str] = []
    parsed = urlparse(issuer)
    if "<" in issuer or ">" in issuer:
        return [f"JWT_ISSUER still contains a placeholder: {issuer}"]
    if parsed.scheme != "https" or not parsed.netloc:
        return [f"JWT_ISSUER must be an absolute https URL (got {issuer!r})"]
    if issuer.endswith("/"):
        out.append(
            f"JWT_ISSUER has a trailing slash ({issuer!r}) — Supabase mints "
            "iss=https://<ref>.supabase.co/auth/v1 and the comparison is exact, so every "
            "login 401s. Drop the slash"
        )
    if parsed.path.rstrip("/") != "/auth/v1":
        out.append(
            f"JWT_ISSUER path is {parsed.path!r}; Supabase issues iss=<project>/auth/v1"
        )
    if supabase_url:
        expected = f"{supabase_url.rstrip('/')}/auth/v1"
        if issuer.rstrip("/") != expected:
            out.append(f"JWT_ISSUER should be exactly {expected} (got {issuer})")
    return out


def _check_issuer_shape(issuer: str | None, problems: list[str]) -> None:
    if not issuer:
        print(f"  {WARN} AEO__AUTH__JWT_ISSUER unset — tokens from ANY project with a valid "
              "signature are accepted. Pin it to https://<ref>.supabase.co/auth/v1")
        return
    found = issuer_problems(issuer)
    if found:
        for p in found:
            print(f"  {BAD} {p}")
        problems.extend(found)
    else:
        print(f"  {OK} AEO__AUTH__JWT_ISSUER shape is valid ({issuer})")


def _live_checks(supabase_url: str, anon: str, site: str | None, jwks_url: str | None,
                 asym_algs: list[str], problems: list[str], *,
                 secret_set: bool = False, issuer: str | None = None) -> None:
    """Probe the running Supabase project. Appends to `problems`; prints as it goes."""
    supabase_url = supabase_url.rstrip("/")
    print("\nlive project probe")

    # 1. Is the project up, and is Google actually enabled on it?
    try:
        status, _, body = _get(f"{supabase_url}/auth/v1/settings", {"apikey": anon})
    except Exception as exc:
        print(f"  {BAD} cannot reach {urlparse(supabase_url).hostname}: {exc}")
        problems.append("Supabase project unreachable")
        return
    if status != 200:
        print(f"  {BAD} GET /auth/v1/settings -> {status} (is the anon key right for this project?)")
        problems.append(f"/auth/v1/settings returned {status}")
        return
    settings = json.loads(body)
    external = settings.get("external", {})
    if external.get("google"):
        print(f"  {OK} Google provider enabled on the project")
    else:
        print(f"  {BAD} Google provider is DISABLED (Authentication -> Providers -> Google)")
        problems.append("Google provider disabled in Supabase")

    # 2. JWKS: does it resolve, and does it carry a key the backend is willing to accept?
    #    An asymmetric project answers with keys; a legacy shared-secret project answers
    #    `{"keys":[]}` — in which case JWKS mode can never verify anything and the backend
    #    needs AEO__AUTH__JWT_SECRET instead. Both look identical from the env vars alone.
    probe_jwks = jwks_url or f"{supabase_url}/auth/v1/.well-known/jwks.json"
    try:
        status, _, body = _get(probe_jwks, {"apikey": anon})
        keys = json.loads(body).get("keys", []) if status == 200 else []
    except Exception as exc:
        status, keys = 0, []
        print(f"  {BAD} JWKS fetch failed ({exc})")
    if status != 200:
        print(f"  {BAD} JWKS {probe_jwks} -> {status}; every login will 401")
        problems.append("configured JWKS URL does not resolve")
    elif not keys:
        print(f"  {BAD} JWKS resolves but is EMPTY — this project signs with the legacy shared "
              "secret, so JWKS mode verifies nothing. Set AEO__AUTH__JWT_SECRET instead.")
        problems.append("JWKS URL returns no keys (project is not using asymmetric signing keys)")
    else:
        algs = sorted({k.get("alg") for k in keys if k.get("alg")})
        print(f"  {OK} JWKS resolves — {len(keys)} key(s), alg(s) {', '.join(algs)}")
        missing = [a for a in algs if a not in asym_algs]
        if missing:
            print(f"  {BAD} the project signs with {', '.join(missing)} but the backend only accepts "
                  f"{', '.join(asym_algs)} — add it to AuthCfg.jwt_asymmetric_algorithms")
            problems.append(f"backend does not accept the project's signing alg ({', '.join(missing)})")
        # The MISSING direction, and the false green this whole item exists for. The check
        # above catches "JWKS configured but the project has no keys". The inverse — the
        # project signs asymmetrically but the backend is configured secret-ONLY — was
        # invisible: every env var looks set, auth_active() is True, the summary printed
        # "gating is configured", and every single real login 401s. api/auth.py only adds
        # ES256/RS256 to the accepted algorithms when a JWKS URL is set (deliberately, to
        # block RS256->HS256 key confusion), so with no JWKS URL there is no key that can
        # verify anything this project mints.
        if not jwks_url:
            print(f"  {BAD} this project signs with {', '.join(algs)} (asymmetric) but "
                  "AEO__AUTH__JWKS_URL is NOT set. The backend would try to verify with the "
                  "shared secret and reject EVERY real login with a generic 'invalid token'.")
            print(f"       Fix: AEO__AUTH__JWKS_URL={supabase_url}/auth/v1/.well-known/jwks.json")
            if secret_set:
                print("       (AEO__AUTH__JWT_SECRET alone cannot verify an asymmetric project — "
                      "a legacy secret is not the signing key here.)")
            problems.append(
                "project uses asymmetric signing keys but AEO__AUTH__JWKS_URL is unset — "
                "every login will 401"
            )

    # The issuer the project's OWN tokens will carry. /auth/v1/settings does not report it,
    # but it is `<project>/auth/v1` by construction — so a configured pin can be compared
    # against the live project rather than only against its own shape.
    if issuer:
        expected = f"{supabase_url}/auth/v1"
        if issuer.rstrip("/") != expected:
            print(f"  {BAD} AEO__AUTH__JWT_ISSUER is {issuer!r} but this project mints "
                  f"iss={expected!r} — the comparison is exact, so every login 401s")
            problems.append(f"JWT_ISSUER does not match the live project (expected {expected})")
        else:
            print(f"  {OK} AEO__AUTH__JWT_ISSUER matches the live project's iss")

    # 3. The one that actually breaks Google sign-in in production.
    if not site:
        print(f"  {WARN} no --site given — skipping the Redirect URLs allowlist check "
              "(this is the check that catches a working config that still can't sign anyone in)")
        return
    site = site.rstrip("/")
    callback = f"{site}/auth/callback"
    try:
        allowed, location = _probe_redirect_allowlist(supabase_url, anon, callback)
    except Exception as exc:
        print(f"  {WARN} allowlist probe inconclusive: {exc}")
        return
    if allowed:
        print(f"  {OK} {callback} is in Redirect URLs — the OAuth return leg lands on your site")
        return
    landed = _canonical(location)
    print(f"  {BAD} {callback} is NOT in the project's Redirect URLs allowlist.")
    print(f"       After Google consent, GoTrue discards it and sends the user to {landed}")
    print("       (that is the project's Site URL). Sign-in silently never completes.")
    print( "       Fix in Supabase -> Authentication -> URL Configuration:")
    print(f"         Site URL      = {site}")
    print(f"         Redirect URLs += {site}/**")
    problems.append(f"{callback} not allowlisted — Google sign-in bounces to {landed}")


def verify_real_token(token: str) -> tuple[bool, list[str]]:
    """Run a REAL access token through the API's own verification path and report which
    check rejected it. Returns (ok, lines_to_print).

    This closes the one gap no read-only probe can: every other check in this file inspects
    configuration, and configuration can be entirely correct-looking while the backend
    rejects every genuine login. The failure is unfalsifiable from outside because
    ``get_current_user`` answers "invalid token" to a forged token, an issuer mismatch, an
    unmatched kid and an expired token alike — by design, so a prober learns nothing.

    So this deliberately imports the SAME functions the API uses (``api.auth._decode`` and
    ``_user_from_claims``) rather than reimplementing verification. A separate implementation
    could agree with the API while both are wrong, or disagree while the API is right; either
    way it would answer a different question than "would this token work?".

    NEVER prints the token, any claim value that identifies the human (email, sub), or any
    secret — only which check failed and the non-secret JOSE header bits, which anyone
    holding the token can already read.
    """
    import jwt as _jwt

    lines: list[str] = []
    sys.path.insert(0, str(ROOT / "src"))
    from aeo.api.auth import _decode, _user_from_claims, auth_active
    from aeo.settings import get_settings

    get_settings.cache_clear()
    cfg = get_settings().auth

    if not auth_active():
        lines.append(f"  {BAD} auth is INACTIVE (no JWT_SECRET and no JWKS_URL) — the backend "
                     "would not verify this or any token; every caller is anonymous")
        return False, lines

    # Unverified header first: `alg`/`kid` are what distinguish "our JWKS lacks this key"
    # from "this is not even one of our tokens", and both are public metadata.
    try:
        header = _jwt.get_unverified_header(token)
    except Exception as exc:
        lines.append(f"  {BAD} not a parseable JWT ({type(exc).__name__}) — did the paste get "
                     "truncated, or is this a refresh token rather than an access token?")
        return False, lines
    alg, kid = header.get("alg"), header.get("kid")
    mode = "JWKS (asymmetric)" if (cfg.jwks_url and alg in cfg.jwt_asymmetric_algorithms) else "shared secret"
    lines.append(f"  {OK} token header: alg={alg} kid={kid or '-'} → would verify via {mode}")

    # The asymmetric-project-with-secret-only false green, caught on a real token: without a
    # JWKS URL, api/auth._algorithms() never even offers ES256/RS256, so there is no key that
    # could verify this.
    if alg in cfg.jwt_asymmetric_algorithms and not cfg.jwks_url:
        lines.append(f"  {BAD} this token is signed with {alg} (asymmetric) but "
                     "AEO__AUTH__JWKS_URL is not set. The backend accepts asymmetric "
                     "algorithms ONLY when a JWKS URL is configured, so every login 401s. "
                     "Set AEO__AUTH__JWKS_URL to the project's .well-known/jwks.json.")
        return False, lines
    if alg in ("HS256", "HS384", "HS512") and not cfg.jwt_secret and cfg.jwks_url:
        lines.append(f"  {BAD} token is signed with {alg} (shared secret) but only "
                     "AEO__AUTH__JWKS_URL is configured — set AEO__AUTH__JWT_SECRET instead")
        return False, lines

    try:
        claims = _decode(token, cfg)
    except Exception as exc:
        name = type(exc).__name__
        # Name the SPECIFIC check. This is the whole point of the mode: "invalid token" is
        # what the API says to a prober, and it is exactly what has made this unfalsifiable.
        if name == "ExpiredSignatureError":
            lines.append(f"  {WARN} the token is EXPIRED. That is not a config problem — "
                         "sign in again and paste a fresh one (they last ~1 hour).")
        elif name == "InvalidIssuerError":
            lines.append(f"  {BAD} ISSUER MISMATCH. AEO__AUTH__JWT_ISSUER is "
                         f"{cfg.jwt_issuer!r} but the token was minted by a different issuer. "
                         "Check for a trailing slash — the comparison is exact.")
        elif name == "InvalidAudienceError":
            lines.append(f"  {BAD} AUDIENCE MISMATCH. Expected aud={cfg.jwt_aud!r}. A Supabase "
                         "END-USER token carries aud='authenticated'; the anon and "
                         "service_role keys do not — did you paste one of those?")
        elif name in ("InvalidSignatureError", "InvalidKeyError", "InvalidAlgorithmError"):
            lines.append(f"  {BAD} SIGNATURE rejected ({name}) — the configured key belongs to "
                         "a different project, or the algorithm is not in the accepted list "
                         f"({', '.join(cfg.jwt_algorithms)}"
                         f"{', ' + ', '.join(cfg.jwt_asymmetric_algorithms) if cfg.jwks_url else ''}).")
        elif "PyJWKClient" in name or "signing key" in str(exc).lower():
            lines.append(f"  {BAD} NO MATCHING KEY. The project's JWKS has no key with "
                         f"kid={kid!r}, or the key set could not be fetched from "
                         f"{cfg.jwks_url}. A rotated-out or wrong-project token looks like this.")
        elif name == "MissingRequiredClaimError":
            lines.append(f"  {BAD} the token is missing a required claim ({exc}) — 'exp' and "
                         "'sub' are both mandatory.")
        else:
            lines.append(f"  {BAD} rejected by signature/claim verification: {name}: {exc}")
        return False, lines

    # Signature and the registered claims are fine; now the checks that actually distinguish
    # a real end-user token from the project's own public anon key (also a valid JWT signed
    # with the same secret — this is the load-bearing part of the whole auth model).
    try:
        _user_from_claims(claims)
    except ValueError as exc:
        lines.append(f"  {BAD} verified, but NOT an end-user token: {exc}. The anon and "
                     "service_role keys are valid JWTs signed with the same secret; "
                     "role='authenticated' + a UUID sub is what separates a real login.")
        return False, lines

    lines.append(f"  {OK} signature verified via {mode}"
                 + (f" (matched kid={kid})" if kid else ""))
    lines.append(f"  {OK} aud={cfg.jwt_aud!r}, role='authenticated', sub is a UUID")
    lines.append(f"  {OK} issuer {'matches ' + repr(cfg.jwt_issuer) if cfg.jwt_issuer else 'not pinned (any issuer accepted)'}")
    lines.append(f"  {OK} this token WOULD be accepted — /api/auth/me should return 200.")
    return True, lines


def main(argv: list[str] | None = None) -> int:
    # BEFORE argparse: a Windows console defaults to cp1252, which cannot encode every
    # character used here (this module's docstring is also `--help`'s output). Without this,
    # a diagnostic run dies with a UnicodeEncodeError instead of printing the diagnosis it
    # was run to print — the worst possible failure for a script whose whole job is to say
    # what is wrong.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="also probe the running Supabase project (read-only; creates nothing)")
    ap.add_argument("--site", default=None, metavar="ORIGIN",
                    help="the deployed origin to check the redirect allowlist for, "
                         "e.g. https://aeo-studio-nine.vercel.app")
    ap.add_argument("--supabase-url", default=None,
                    help="override the project URL (default: NEXT_PUBLIC_SUPABASE_URL from web/.env.local)")
    ap.add_argument("--anon-key", default=None,
                    help="override the anon key (default: NEXT_PUBLIC_SUPABASE_ANON_KEY from web/.env.local)")
    ap.add_argument("--token", default=None, metavar="ACCESS_TOKEN",
                    help="verify a REAL access token through the API's own path and report "
                         "which check rejects it. Use '-' to read it from stdin (keeps it out "
                         "of your shell history). The token is never printed or logged.")
    args = ap.parse_args(argv)
    return _run(args)


def _run(args: argparse.Namespace) -> int:
    problems: list[str] = []

    # ── backend ───────────────────────────────────────────────────────────────
    sys.path.insert(0, str(ROOT / "src"))
    from aeo.settings import get_settings

    get_settings.cache_clear()
    cfg = get_settings().auth

    print("backend (.env)")
    secret_set = bool(cfg.jwt_secret)
    jwks_set = bool(cfg.jwks_url)
    if secret_set:
        print(f"  {OK} AEO__AUTH__JWT_SECRET set ({len(cfg.jwt_secret or '')} chars, value not shown)")
    if jwks_set:
        host = urlparse(cfg.jwks_url or "").hostname or "?"
        print(f"  {OK} AEO__AUTH__JWKS_URL set (host {host})")
        if "<project-ref>" in (cfg.jwks_url or ""):
            problems.append("JWKS URL still contains the <project-ref> placeholder")
    if not (secret_set or jwks_set):
        print(f"  {BAD} neither JWT_SECRET nor JWKS_URL set -> auth is OPEN, nothing is gated")
        problems.append("no backend verification key configured")

    if secret_set and jwks_set:
        print(f"  {WARN} both modes set — fine during rotation, otherwise pick one")

    # The issuer pin was never checked here at all. A wrong one rejects EVERY token with the
    # same opaque "invalid token" a forged token gets, so it is indistinguishable from a
    # signature problem from the outside — and jwt.decode compares it as an exact string, so
    # a single trailing slash is a complete outage.
    _check_issuer_shape(cfg.jwt_issuer, problems)

    from aeo.api.auth import auth_active

    print(f"  {OK if auth_active() else BAD} auth_active() = {auth_active()}")
    if not auth_active():
        problems.append("auth_active() is False — the gate is not enforcing")

    if cfg.promo_code_set:
        print(f"  {OK} {len(cfg.promo_code_set)} promo code(s) loaded (redeem -> all_packs)")

    # ── frontend ──────────────────────────────────────────────────────────────
    # The flags exist so a DEPLOYED project can be checked from a dev box: in production
    # these two live on Vercel, not in web/.env.local, so the file is empty and the whole
    # check would otherwise be unrunnable against the environment that actually matters.
    web = _read_env_file(ROOT / "web" / ".env.local")
    source = "web/.env.local"
    url = args.supabase_url or web.get("NEXT_PUBLIC_SUPABASE_URL", "")
    anon = args.anon_key or web.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
    if args.supabase_url or args.anon_key:
        source = "web/.env.local + CLI overrides"
    print(f"\nfrontend ({source})")
    if url:
        host = urlparse(url).hostname or ""
        # The single most common mistake: pasting the DASHBOARD address
        # (https://supabase.com/dashboard/project/<ref>) instead of the project API host.
        # It is non-empty and looks plausible, so only a shape check catches it.
        if host in ("supabase.com", "www.supabase.com") or "/dashboard/" in url:
            print(f"  {BAD} NEXT_PUBLIC_SUPABASE_URL is the DASHBOARD address, not the "
                  "project API URL — it must be https://<ref>.supabase.co")
            problems.append("frontend URL points at the dashboard, not the project API host")
        elif not host.endswith(".supabase.co"):
            print(f"  {WARN} NEXT_PUBLIC_SUPABASE_URL = {host} (expected <ref>.supabase.co; "
                  "fine only if you self-host)")
        else:
            print(f"  {OK} NEXT_PUBLIC_SUPABASE_URL = {host}")
    else:
        print(f"  {BAD} NEXT_PUBLIC_SUPABASE_URL empty -> no Sign-in button will render")
        problems.append("frontend Supabase URL not set")
    if anon:
        print(f"  {OK} NEXT_PUBLIC_SUPABASE_ANON_KEY set ({len(anon)} chars)")
    else:
        print(f"  {BAD} NEXT_PUBLIC_SUPABASE_ANON_KEY empty -> no Sign-in button will render")
        problems.append("frontend anon key not set")

    # ── cross-check: the two halves must describe the SAME project ────────────
    print("\nconsistency")
    if url and jwks_set:
        u_host, j_host = urlparse(url).hostname, urlparse(cfg.jwks_url or "").hostname
        if u_host and j_host and u_host != j_host:
            print(f"  {BAD} frontend project ({u_host}) != JWKS project ({j_host})")
            problems.append("frontend and backend point at different Supabase projects")
        else:
            print(f"  {OK} frontend and JWKS agree on {u_host}")
    elif url and secret_set:
        print(f"  {WARN} can't verify the secret belongs to {urlparse(url).hostname} — "
              "if logins 401, the secret is from another project (or the project is asymmetric)")

    # The issuer must name the SAME project the frontend talks to, exactly. Checked here
    # rather than above because it needs the frontend URL.
    if url and cfg.jwt_issuer:
        cross = issuer_problems(cfg.jwt_issuer, url)
        shape_only = set(issuer_problems(cfg.jwt_issuer))
        for p in cross:
            if p not in shape_only:  # don't repeat what the backend section already printed
                print(f"  {BAD} {p}")
                problems.append(p)
        if not cross:
            print(f"  {OK} JWT_ISSUER matches the frontend project")

    # A Supabase anon key is itself a JWT; a service_role key here would be a real leak.
    if anon and anon.count(".") == 2:
        try:
            payload = anon.split(".")[1]
            decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            # A legacy anon key carries its own project ref — the authoritative
            # cross-check that URL, JWKS and key all describe the SAME project.
            key_ref = decoded.get("ref")
            if key_ref:
                for label, value in (("frontend URL", url), ("JWKS URL", cfg.jwks_url or "")):
                    v_host = urlparse(value).hostname or ""
                    if value and v_host.endswith(".supabase.co") and not v_host.startswith(f"{key_ref}."):
                        print(f"  {BAD} {label} host ({v_host}) != the anon key's project "
                              f"({key_ref}.supabase.co)")
                        problems.append(f"{label} belongs to a different project than the anon key")
                print(f"  {OK} anon key project ref = {key_ref}")

            role = decoded.get("role")
            if role == "service_role":
                print(f"  {BAD} that key is a SERVICE_ROLE key — it bypasses RLS and must "
                      "NEVER ship to the browser. Use the anon/publishable key.")
                problems.append("service_role key in NEXT_PUBLIC_SUPABASE_ANON_KEY")
            elif role:
                print(f"  {OK} anon key role = {role}")
        except Exception:
            pass  # newer sb_publishable_* keys aren't JWTs — nothing to check
    elif anon and re.match(r"^sb_secret_", anon):
        print(f"  {BAD} that is a SECRET key — never put it in a NEXT_PUBLIC_* var")
        problems.append("secret key in NEXT_PUBLIC_SUPABASE_ANON_KEY")

    # ── live ──────────────────────────────────────────────────────────────────
    if args.live:
        if url and anon:
            _live_checks(url, anon, args.site, cfg.jwks_url,
                         list(cfg.jwt_asymmetric_algorithms), problems,
                         secret_set=secret_set, issuer=cfg.jwt_issuer)
        else:
            print(f"\n{WARN} --live needs a project URL + anon key "
                  "(web/.env.local, or --supabase-url/--anon-key)")
    else:
        print(f"\n{WARN} env-only check. The settings that actually route the Google return leg "
              "live in the Supabase dashboard,\n     not in any file here — re-run with "
              "`--live --site <your deployed origin>` to check those too.")

    # ── a REAL token (--token) ────────────────────────────────────────────────
    # The only check here that can prove the backend accepts a genuine login rather than
    # inferring it from configuration.
    if args.token:
        token = args.token.strip()
        if token == "-":
            print("\npaste the access token, then press Enter:")
            token = sys.stdin.readline().strip()
        token = token.removeprefix("Bearer ").removeprefix("bearer ").strip().strip('"').strip("'")
        print("\nreal token verification")
        if not token:
            print(f"  {BAD} no token supplied")
            problems.append("no token supplied to --token")
        else:
            ok, lines = verify_real_token(token)
            for line in lines:
                print(line)
            if not ok:
                problems.append("a REAL access token was REJECTED by the API's own "
                                "verification path (see above for which check)")

    print()
    if problems:
        print(f"{BAD} not ready — {len(problems)} problem(s):")
        for p in problems:
            print(f"     - {p}")
        return 1
    print(f"{OK} gating is configured. Restart both servers, then: sign in, open a run's "
          "packs — Pack 2+ should read 'locked' and its detail should 403.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
