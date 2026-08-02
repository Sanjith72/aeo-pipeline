#!/usr/bin/env python
"""Verify the v5 auth/gating config is actually live (CH-07/CH-02a).

Run after filling in .env + web/.env.local:

    python scripts/check_auth_config.py

Reports which verification mode is active and whether the frontend half is configured.
Prints only booleans, lengths and hostnames — NEVER a secret value — so the output is
safe to paste into an issue or a chat.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OK, BAD, WARN = "[ok]", "[!!]", "[--]"


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


def main() -> int:
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

    from aeo.api.auth import auth_active

    print(f"  {OK if auth_active() else BAD} auth_active() = {auth_active()}")
    if not auth_active():
        problems.append("auth_active() is False — the gate is not enforcing")

    if cfg.promo_code_set:
        print(f"  {OK} {len(cfg.promo_code_set)} promo code(s) loaded (redeem -> all_packs)")

    # ── frontend ──────────────────────────────────────────────────────────────
    web = _read_env_file(ROOT / "web" / ".env.local")
    print("\nfrontend (web/.env.local)")
    url = web.get("NEXT_PUBLIC_SUPABASE_URL", "")
    anon = web.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
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

    # A Supabase anon key is itself a JWT; a service_role key here would be a real leak.
    if anon and anon.count(".") == 2:
        import base64
        import json

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
