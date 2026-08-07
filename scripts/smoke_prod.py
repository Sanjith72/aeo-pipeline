#!/usr/bin/env python3
"""Post-deploy smoke test against PRODUCTION. Read-only.

    python scripts/smoke_prod.py --base https://sanjith12-aeo-api.hf.space \
                                 --site https://aeo-studio-nine.vercel.app

Why this exists: nothing in this repo deploys itself. The backend ships on a MANUAL Hugging
Face factory rebuild (a plain restart silently keeps the old layer) and the frontend on a
Vercel push, so "it passes locally" and "it works in production" are unrelated statements.
This is the thing you run after every deploy to turn that into an answer.

**Read-only, and it stays that way.** It never signs up, never charges, never grants, never
closes a ticket. Every check either GETs, or POSTs something designed to be REJECTED before
it can act (an unsigned webhook fails HMAC before the body is parsed; a mutation aimed at a
run id that does not exist 404s before touching a row). The one genuinely expensive check --
building a fresh overview, which spends crawl and LLM budget and consumes a slot in the
daily free-tier cap -- is OFF unless you pass --overview-domain.

Each check prints what it PROVES and, where it matters, what it does not. A green line that
means less than it looks like is worse than no line at all.

Exit code is 0 only when every check that ran passed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

TIMEOUT = 45.0
_CTX = ssl.create_default_context()


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    proves: str
    caveat: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    #: (name, why) for every check that did NOT run. Tracked, not just printed, because the
    #: denominator has to know about them — see `summarise`.
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def add(self, r: Result) -> Result:
        self.results.append(r)
        mark = "PASS" if r.ok else "FAIL"
        print(f"[{mark}] {r.name}: {r.detail}")
        print(f"       proves: {r.proves}")
        if r.caveat:
            print(f"       NOT proved: {r.caveat}")
        return r

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((name, why))
        print(f"[SKIP] {name}: {why}")

    def summarise(self) -> int:
        """Print the tally and return the exit code.

        A skipped check is reported in the DENOMINATOR, never quietly dropped from it. This
        used to print "7/7 passed" and "all good" on a run where the build-id comparison had
        silently not happened — the single check the whole script exists for — because `aeo`
        was not importable from the interpreter it was invoked with. A tally that counts only
        the checks that ran is a false green, which is the exact failure this script is
        supposed to catch in others.
        """
        failed = [r for r in self.results if not r.ok]
        total = len(self.results) + len(self.skipped)
        print(f"\n{len(self.results) - len(failed)}/{total} passed"
              f"{f', {len(failed)} failed' if failed else ''}"
              f"{f', {len(self.skipped)} SKIPPED' if self.skipped else ''}")
        if failed:
            print("\nFAILED:")
            for r in failed:
                print(f"  - {r.name}: {r.detail}")
        if self.skipped:
            print("\nDID NOT RUN (so this run proves nothing about them):")
            for name, why in self.skipped:
                print(f"  - {name}: {why}")
        if failed:
            return 1
        print("\nEverything that ran, passed. Still unproved by any automated check: a real"
              "\nGoogle sign-in, a real Stripe purchase, and cross-user ticket ownership --"
              "\nall three need a human with credentials.")
        return 0


def request(
    url: str, *, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None
) -> tuple[int, str]:
    """(status, body). A 4xx/5xx is data here, not an exception -- most checks EXPECT one."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # DNS, TLS, timeout -- a real outage, reported as status 0
        return 0, f"{type(e).__name__}: {e}"


def as_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# ── checks ─────────────────────────────────────────────────────────────────────────────


def check_health(rep: Report, base: str, expect_build: str | None) -> None:
    status, text = request(f"{base}/api/health")
    body = as_json(text)
    ok = status == 200 and body.get("status") == "ok"
    rep.add(
        Result(
            "backend health",
            ok,
            f"HTTP {status} | db={body.get('db', '?')} | build={body.get('build', 'absent')}",
            "the API process is up and can reach Postgres",
            "" if body.get("db") == "ok" else "database reachability -- db is NOT ok",
        )
    )
    if expect_build is None:
        return
    got = str(body.get("build", ""))
    if not got:
        rep.add(
            Result(
                "deployed build id",
                False,
                "the deployed /api/health reports no `build` field",
                "nothing -- this backend predates build-id reporting",
                "which commit is running. Factory-rebuild from a commit that has src/aeo/build.py",
            )
        )
        return
    rep.add(
        Result(
            "deployed build id",
            got == expect_build,
            f"deployed={got} expected={expect_build}"
            + ("" if got == expect_build else "  <- the rebuild did NOT take"),
            "the Space is running exactly this checkout's backend code",
            ""
            if got == expect_build
            else "a plain restart reuses the cached layer; use Settings -> Factory rebuild",
        )
    )


def check_api_is_gated(rep: Report, base: str) -> None:
    status, _ = request(f"{base}/api/packs/999999")
    rep.add(
        Result(
            "service key enforced",
            status == 401,
            f"GET /api/packs/999999 without X-API-Key -> HTTP {status} (want 401)",
            "AEO__API__AUTH_KEY is set on the Space, so /api/* is not open to the internet",
        )
    )


def check_grant_route_not_public(rep: Report, base: str) -> None:
    """The paywall bypass this repo worries about most: the Vercel proxy denylists this path,
    but the Space's own URL bypasses the proxy entirely."""
    status, _ = request(
        f"{base}/api/entitlements/grant",
        method="POST",
        body=b"{}",
        headers={"content-type": "application/json"},
    )
    rep.add(
        Result(
            "entitlement grant not public",
            status != 200,
            f"POST /api/entitlements/grant direct to the backend -> HTTP {status} (want 401/403/503)",
            "an anonymous caller cannot mint themselves entitlements on the backend's own URL",
        )
    )


def check_webhook_rejects_unsigned(rep: Report, base: str) -> None:
    """400, specifically. A 401 would mean the route lost its X-API-Key exemption -- Stripe
    cannot send that header, so every real delivery would fail and no payment would ever
    become an entitlement."""
    status, _ = request(
        f"{base}/api/webhooks/stripe",
        method="POST",
        body=b'{"type":"checkout.session.completed"}',
        headers={"content-type": "application/json"},
    )
    rep.add(
        Result(
            "stripe webhook rejects unsigned",
            status == 400,
            f"POST /api/webhooks/stripe with no signature -> HTTP {status} (want 400)",
            "the webhook verifies the HMAC before parsing, and is reachable without the service key",
            "that YOUR endpoint secret is correct -- only a real signed delivery shows that",
        )
    )


def check_web_proxy(rep: Report, site: str) -> None:
    status, text = request(f"{site}/api/config")
    body = as_json(text)
    if status == 503:
        rep.add(
            Result(
                "web -> backend proxy",
                False,
                f"HTTP 503 | {body.get('detail', text)[:120]}",
                "the proxy is running but has no API_BASE_URL for this environment",
                "set API_BASE_URL + API_KEY with Production + Preview + Development ticked",
            )
        )
        return
    keys = ("payments_enabled", "promo_enabled", "auth_enabled")
    ok = status == 200 and all(k in body for k in keys)
    caps = " ".join(f"{k.split('_')[0]}={body.get(k)}" for k in keys) if ok else ""
    rep.add(
        Result(
            "web -> backend proxy",
            ok,
            f"GET {site}/api/config -> HTTP {status} {caps}".rstrip(),
            "the deployed web origin reaches the backend and injects a service key it accepts",
        )
    )
    if ok and body.get("unknown"):
        rep.add(
            Result(
                "capability probe",
                False,
                "/api/config returned unknown:true -- these are optimistic defaults, not facts",
                "nothing about what is actually configured",
                "whether payments/promo are really on; the backend probe failed",
            )
        )


def check_mutation_needs_auth(rep: Report, base: str) -> None:
    """Aimed at the BACKEND directly and with no key, so the request dies at the service-key
    guard. Deliberately not sent through the proxy: there the key IS injected, and a close
    aimed at a real unowned board could actually mutate data."""
    status, _ = request(
        f"{base}/api/tickets/999999999/close",
        method="POST",
        body=b'{"task_key":"smoke-test-never-exists"}',
        headers={"content-type": "application/json"},
    )
    rep.add(
        Result(
            "ticket mutation needs a key",
            status in (401, 403),
            f"POST /api/tickets/.../close unauthenticated -> HTTP {status} (want 401/403)",
            "ticket mutations are behind the service-key guard on the public backend URL",
            "that user A cannot close user B's tickets -- that needs two real user tokens "
            "(Phase 6 item 14), and no anonymous probe can stand in for it",
        )
    )


def check_landing(rep: Report, site: str) -> None:
    status, text = request(site)
    rep.add(
        Result(
            "web origin serves",
            status == 200 and "AEO" in text,
            f"GET {site} -> HTTP {status}",
            "Vercel is serving the app (not a build error page)",
        )
    )


def check_overview(rep: Report, site: str, domain: str) -> None:
    """OPT-IN. A fresh build spends real crawl + LLM budget and burns one of the day's
    free-tier slots, so it is never part of the default run."""
    status, text = request(
        f"{site}/api/overview",
        method="POST",
        body=json.dumps({"domain": domain}).encode(),
        headers={"content-type": "application/json"},
    )
    body = as_json(text)
    packs = body.get("packs") or []
    first = next((p for p in packs if p.get("pack_index") == 1), None)
    deeper = [p for p in packs if (p.get("pack_index") or 0) > 1]
    ok = (
        status == 200
        and first is not None
        and first.get("locked") is False
        and bool(deeper)
        and all(p.get("locked") for p in deeper)
    )
    rep.add(
        Result(
            "anonymous pack gating",
            ok,
            f"POST /api/overview({domain}) -> HTTP {status} | "
            f"{len(packs)} packs | pack1 locked={first.get('locked') if first else 'n/a'} | "
            f"deeper all locked={all(p.get('locked') for p in deeper) if deeper else 'n/a'}",
            "the free tier gives an anonymous visitor Pack 1 and gates every deeper pack",
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="backend origin, e.g. https://sanjith12-aeo-api.hf.space")
    ap.add_argument("--site", required=True, help="web origin, e.g. https://aeo-studio-nine.vercel.app")
    ap.add_argument(
        "--expect-build",
        help="build id production should report. Default: this checkout's, via aeo.build.build_id",
    )
    ap.add_argument("--skip-build-check", action="store_true", help="do not compare build ids")
    ap.add_argument(
        "--overview-domain",
        help="OPT-IN: also verify anonymous pack gating by building a FRESH overview for this "
        "domain. Spends crawl + LLM budget and one free-tier slot.",
    )
    args = ap.parse_args()

    # A Windows console defaults to cp1252, which raises UnicodeEncodeError on any character
    # outside it. A smoke test that dies mid-report because of a punctuation mark reports
    # nothing about production, so never let the terminal's encoding be a failure mode.
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    base = args.base.rstrip("/")
    site = args.site.rstrip("/")

    print(f"smoke: backend {base}\n       web     {site}\n")
    rep = Report()

    # Resolve the expected build id BEFORE anything else, and record a SKIP if we cannot.
    # `aeo` is importable only from an interpreter that has this package installed — run the
    # script with the project venv's python (.venv/Scripts/python.exe on Windows), not a bare
    # system python, or this check quietly does not happen.
    expect: str | None = None
    if args.skip_build_check:
        rep.skip("deployed build id", "--skip-build-check was passed")
    else:
        expect = args.expect_build
        if expect is None:
            try:
                from aeo.build import build_id

                expect = build_id()
            except Exception:
                rep.skip(
                    "deployed build id",
                    "`aeo` is not importable from this interpreter, so there is nothing to "
                    "compare against -- WHICH CODE IS DEPLOYED IS THEREFORE UNKNOWN. Re-run "
                    "with the project venv's python, or pass --expect-build <id>",
                )

    check_health(rep, base, expect)
    check_api_is_gated(rep, base)
    check_grant_route_not_public(rep, base)
    check_webhook_rejects_unsigned(rep, base)
    check_mutation_needs_auth(rep, base)
    check_landing(rep, site)
    check_web_proxy(rep, site)
    if args.overview_domain:
        check_overview(rep, site, args.overview_domain)
    else:
        rep.skip("anonymous pack gating", "needs --overview-domain (spends crawl + LLM budget)")

    return rep.summarise()


if __name__ == "__main__":
    sys.exit(main())
