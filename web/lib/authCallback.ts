// v5 CH-07 — the pure decision logic behind /auth/callback and the signup notices.
//
// Split out of the route component on purpose. The callback has to handle four DIFFERENT
// link shapes, each with its own failure modes, and the difference between them is exactly
// what decides whether a user gets signed in or stares at a spinner for 15 seconds. That
// logic is worth testing, and a React component that reaches for `window` and a live
// Supabase client is not testable under this project's runner (node --test with type
// stripping — no DOM, no React renderer). So everything that can be decided from strings
// lives here and is covered by authCallback.test.ts; page.tsx keeps only the effect wiring.

/** The subset of Supabase's EmailOtpType we can arrive with. Declared locally rather than
 *  imported so this module stays dependency-free and loadable by the bare node test runner. */
export type EmailOtpKind = "signup" | "invite" | "magiclink" | "recovery" | "email_change" | "email";

const EMAIL_OTP_KINDS: readonly string[] = [
  "signup", "invite", "magiclink", "recovery", "email_change", "email",
];

export type CallbackShape =
  /** GoTrue or the provider reported a failure outright (query OR fragment). */
  | { kind: "provider_error"; code: string | null; description: string }
  /** The confirm-email link shape: ?token_hash=...&type=... — verified with verifyOtp. */
  | { kind: "token_hash"; tokenHash: string; type: EmailOtpKind }
  /** The PKCE OAuth return: ?code=... — supabase-js exchanges it via detectSessionInUrl. */
  | { kind: "code" }
  /** The legacy implicit shape: #access_token=... — supabase-js also consumes this. */
  | { kind: "implicit_tokens" }
  /** Nothing actionable in the URL. */
  | { kind: "none" };

/** Parse a `?a=b` or `#a=b` string into a lookup. Tolerates a leading `?`/`#` and an
 *  empty string. Used for the hash because GoTrue puts implicit-flow results — including
 *  its ERRORS — in the fragment, which the previous implementation never looked at. */
export function parseParams(raw: string | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!raw) return out;
  const body = raw.replace(/^[?#]/, "");
  if (!body) return out;
  for (const [k, v] of new URLSearchParams(body).entries()) out[k] = v;
  return out;
}

/**
 * Decide what this callback URL is, from its query and fragment.
 *
 * Order matters and is load-bearing:
 *   1. An explicit error wins over everything. GoTrue reports a refused consent, a
 *      non-allowlisted redirect and an expired link as PARAMS, not exceptions, and it uses
 *      the query for PKCE and the FRAGMENT for the implicit flow — so both must be read or
 *      half the real failures look like "nothing happened".
 *   2. token_hash before code. The confirm-email link is the one shape this app could not
 *      handle at all: it fell through to the PKCE branch, which waits for a session that
 *      no exchange is ever going to produce, and showed a timeout 15 seconds later.
 */
export function readCallback(search: string | null, hash: string | null): CallbackShape {
  const q = parseParams(search);
  const h = parseParams(hash);

  const errDescription = q.error_description ?? h.error_description ?? q.error ?? h.error;
  if (errDescription) {
    return {
      kind: "provider_error",
      code: q.error_code ?? h.error_code ?? q.error ?? h.error ?? null,
      description: errDescription,
    };
  }

  const tokenHash = q.token_hash ?? h.token_hash;
  if (tokenHash) {
    const rawType = q.type ?? h.type ?? "email";
    const type = (EMAIL_OTP_KINDS.includes(rawType) ? rawType : "email") as EmailOtpKind;
    return { kind: "token_hash", tokenHash, type };
  }
  if (q.code) return { kind: "code" };
  if (h.access_token) return { kind: "implicit_tokens" };
  return { kind: "none" };
}

/**
 * Only ever bounce to a path on this origin — an attacker-supplied URL in `next` would turn
 * the login flow into an open redirect, and it fires on an already-authenticated victim.
 *
 * Validate by RESOLUTION, not by prefix. A startsWith("/") + !startsWith("//") check looks
 * right but is bypassable, because the URL parser strips raw control characters BEFORE
 * parsing: "/\r/evil.com" has its CR removed and becomes "//evil.com" — an off-origin URL
 * that passed both halves of the prefix check as written. (Verified in authCallback.test.ts:
 * a tab or newline in "/\tevil.com" is likewise stripped, but that one lands on the
 * harmless same-origin path /evil.com. The point is that you cannot tell which is which by
 * inspecting the string.) Resolving against our own origin and comparing origins is the only
 * check that does not depend on predicting the parser.
 *
 * `origin` is a parameter rather than a `window` read so this stays testable.
 */
export function safeNext(raw: string | null | undefined, origin: string): string {
  if (!raw) return "/";
  try {
    const u = new URL(raw, origin);
    if (u.origin !== new URL(origin).origin) return "/";
    return `${u.pathname}${u.search}${u.hash}`;
  } catch {
    return "/";
  }
}

export type FailureKind =
  /** The link was valid but has already been used, or has aged out. Resending fixes it. */
  | "spent"
  /** Already confirmed — the account works; they should just sign in. */
  | "already_confirmed"
  /** Too many mails requested; waiting fixes it, resending now does not. */
  | "rate_limited"
  /** The redirect target is not in Supabase's allowlist — an operator fix, not a user one. */
  | "not_allowlisted"
  /** The user (or the provider) declined the sign-in. Nothing is broken; they said no. */
  | "declined"
  /** A PKCE code arrived but this browser holds no code_verifier for it — the link was
   *  opened somewhere other than where it was started, or storage was cleared. Resending
   *  does NOT help; opening it in the original browser does. */
  | "verifier_missing"
  /** Nothing resolved in time. Distinct from "spent": we genuinely do not know. */
  | "timeout"
  /** Anything else. */
  | "unknown";

export interface Failure {
  kind: FailureKind;
  title: string;
  message: string;
  /** Whether offering "send me a new link" can actually help. */
  canResend: boolean;
}

/**
 * Turn a Supabase auth error message into a state the UI can render honestly.
 *
 * Why string matching: GoTrue's error CODES are not stable across versions or endpoints
 * (the same expired-link condition arrives as `otp_expired`, `token_expired`, or no code at
 * all with a prose message), so the message is the only signal present in every version. We
 * match loosely and default to "unknown" rather than mislabelling — a wrong-but-confident
 * error is worse than a vague one.
 *
 * The distinction that matters most is `spent`. Confirmation links are single-use, and mail
 * scanners and link-prefetchers routinely open them before the human clicks. To the user,
 * "your link was already used by your employer's security scanner" and "this app is broken"
 * look identical — and only one of them has a fix the user can act on.
 */
export function classifyAuthFailure(raw: string | null | undefined, code?: string | null): Failure {
  const s = `${code ?? ""} ${raw ?? ""}`.toLowerCase();

  if (s.includes("already confirmed") || s.includes("email_change_confirm") ||
      s.includes("already been confirmed")) {
    return {
      kind: "already_confirmed",
      title: "Your email is already confirmed",
      message: "Nothing more to do — sign in with your email and password.",
      canResend: false,
    };
  }
  if (s.includes("rate limit") || s.includes("too many") || s.includes("over_email_send_rate")) {
    return {
      kind: "rate_limited",
      title: "Too many emails for now",
      message: "Supabase is rate-limiting confirmation emails. Wait a minute, then try again.",
      canResend: false,
    };
  }
  // BEFORE the expired/spent branch, deliberately. A non-allowlisted redirect surfaces as
  // `error=invalid_request`, which the old ordering swallowed as "your link expired" — so the
  // user was told to resend an email while the actual fix was a Supabase Redirect URLs
  // change. That is precisely the "three different fixes, one symptom" trap DEPLOY.md warns
  // about, reproduced in the UI.
  if (s.includes("redirect") && (s.includes("allow") || s.includes("not valid") || s.includes("invalid"))) {
    return {
      kind: "not_allowlisted",
      title: "This site isn't allowed to complete sign-in",
      message:
        "The sign-in service rejected this address as a return destination. This is a " +
        "configuration problem on our side, not something you can fix — please let us know.",
      canResend: false,
    };
  }
  // Matching here is on LINK-SPECIFIC phrases only. It used to include a bare `"used"`, which
  // matches the substring inside "ref-used", and a bare `"invalid"`, which matches Supabase's
  // generic `invalid_request` / "Requested path is invalid". Both routed unrelated provider
  // errors into "that link has expired" — verified in production, where a refused Google
  // consent rendered the email-expiry copy with a Resend button that could not possibly help.
  if (s.includes("expired") || s.includes("otp_expired") || s.includes("token not found") ||
      s.includes("token_not_found") || s.includes("already been used") ||
      s.includes("already used") || s.includes("email link is invalid") ||
      s.includes("link is invalid") || s.includes("invalid or has expired")) {
    return {
      kind: "spent",
      title: "That link has expired",
      message:
        "Confirmation links can only be used once, and email security scanners often open " +
        "them before you get the chance. Send yourself a fresh one and it will work.",
      canResend: true,
    };
  }
  // AFTER "spent", and the order is load-bearing in the opposite direction to what it looks
  // like. `access_denied` is Google's code for a refused consent — but Supabase ALSO wraps an
  // expired confirmation link in it:
  //     #error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired
  // Putting this branch first (as the first draft of this fix did) would have relabelled every
  // expired email link as "Sign-in was cancelled" and dropped the Resend button that is the
  // actual remedy — trading one misclassification for a worse one. Expiry evidence is
  // specific, so it gets to answer first; this branch takes what is left.
  if (s.includes("access_denied") || s.includes("consent") || s.includes("denied") ||
      s.includes("cancelled") || s.includes("canceled")) {
    return {
      kind: "declined",
      title: "Sign-in was cancelled",
      message:
        "You didn't finish signing in with that provider. Nothing has gone wrong — start " +
        "again whenever you're ready.",
      canResend: false,
    };
  }
  return {
    kind: "unknown",
    title: "Sign-in didn't complete",
    message: raw?.trim() || "Something went wrong finishing your sign-in. Please try again.",
    canResend: true,
  };
}

/** Wording for a failed `supabase.auth.signUp`. Today this surfaces the raw exception
 *  string, so "User already registered" — the single most common and most fixable case —
 *  reads like an internal error rather than "you already have an account". */
export function classifySignUpFailure(raw: string | null | undefined): Failure {
  const s = (raw ?? "").toLowerCase();
  if (s.includes("already registered") || s.includes("user already") ||
      s.includes("already exists")) {
    return {
      kind: "already_confirmed",
      title: "You already have an account",
      message: "That email is already registered — sign in instead, or reset your password.",
      canResend: false,
    };
  }
  if (s.includes("password")) {
    return {
      kind: "unknown",
      title: "That password won't work",
      message: raw?.trim() || "Please choose a password with at least 6 characters.",
      canResend: false,
    };
  }
  return classifyAuthFailure(raw);
}

/** Seconds a user must wait between confirmation-email resends. Supabase's own default
 *  rate limit is one per 60s, so a shorter cooldown only produces a rate-limit error the
 *  user cannot act on. */
export const RESEND_COOLDOWN_SEC = 60;

/** Remaining cooldown in whole seconds; 0 when a resend is allowed. Pure, so the UI's timer
 *  and the disabled state are computed from one rule instead of drifting apart. */
export function resendCooldownRemaining(
  lastSentAtMs: number | null, nowMs: number, cooldownSec: number = RESEND_COOLDOWN_SEC,
): number {
  if (lastSentAtMs === null) return 0;
  const elapsed = Math.floor((nowMs - lastSentAtMs) / 1000);
  return Math.max(0, cooldownSec - elapsed);
}

// ── the two PKCE outcomes that used to be indistinguishable ─────────────────────────
//
// The callback called `classifyAuthFailure("The sign-in link has expired or was already
// used.")` — the SAME hardcoded string — from two genuinely different places: the
// INITIAL_SESSION fast path (a code arrived, initialization produced no session) and the 15s
// timeout (nothing resolved at all). Byte-identical argument, so the user saw byte-identical
// copy. The real reason went to console.error, where no user will ever look. The item this
// closes asked that failures surface a REAL reason; "not just a timeout" was achieved, but
// "a reason" was not.
//
// These two are not the same event and must not read the same:
//
//  * verifier_missing — the PKCE code_verifier lives in THIS browser's storage. If the link
//    is opened in a different browser, on a different device, or after storage was cleared,
//    the code cannot be exchanged no matter how fresh it is. Resending is useless; opening
//    the link where it was started is the fix. Calling this "expired" sends the user round a
//    loop that cannot terminate.
//  * timeout — we genuinely do not know. Say so rather than asserting a cause.

/** A PKCE code was present but produced no session: already spent, or no verifier here. */
export function verifierMissingFailure(): Failure {
  return {
    kind: "verifier_missing",
    title: "This link needs the browser you started in",
    message:
      "Sign-in links carry a secret that stays in the browser that requested them, so they " +
      "can't be completed somewhere else — a different browser or device, a private window, " +
      "or after clearing site data. Open the link in the browser where you started, or just " +
      "sign in again here. (If you already used this link once, that also uses it up.)",
    canResend: true,
  };
}

/** Nothing resolved inside the window. An honest "we don't know", not a guessed cause. */
export function timeoutFailure(): Failure {
  return {
    kind: "timeout",
    title: "Sign-in didn't finish in time",
    message:
      "We didn't hear back from the sign-in service. That's usually a network hiccup rather " +
      "than a problem with your account — please try signing in again.",
    canResend: false,
  };
}

// ── resending when we do not know the address ──────────────────────────────────────
//
// The callback's resend button was gated on `failure.canResend && email`, and `email` is null
// on EVERY failure path: the provider_error branch returns before it is set, and the
// verifyOtp branch calls setEmail(null) on exactly the failure that sets canResend:true. So a
// spent confirmation link rendered "Send yourself a fresh one and it will work" above a single
// "Go back" button. The copy promised a remedy the UI never offered — worse than saying
// nothing, because the user concludes the product is broken rather than that they must act.
//
// The address genuinely IS unknown at that point: verifyOtp failed, so no user came back. It
// cannot be recovered — it has to be asked for. Hence the check below, and a form.

/** Does this look enough like an email address to be worth sending to Supabase?
 *
 *  Deliberately permissive. This is a pre-flight so the button can be disabled on obvious
 *  nonsense and the user gets an instant answer instead of a round trip; the authority on
 *  deliverability is the mail server, and over-strict client validation is how legitimate
 *  addresses (plus-tags, long TLDs, unicode locals) get rejected by software that thinks it
 *  knows better. */
export function looksLikeEmail(raw: string | null | undefined): boolean {
  const s = (raw ?? "").trim();
  if (!s || s.length > 320 || /\s/.test(s)) return false;
  const at = s.indexOf("@");
  if (at <= 0 || at !== s.lastIndexOf("@")) return false;
  const domain = s.slice(at + 1);
  if (!domain || domain.startsWith(".") || domain.endsWith(".")) return false;
  return domain.includes(".");
}

/** Supabase's `resend()` accepts a narrower set of types than a callback link can carry.
 *  A recovery link is not resendable this way at all (that is resetPasswordForEmail), so it
 *  maps to the signup default rather than throwing — the caller only reaches this when the
 *  classifier has already said a resend can help. */
export function resendTypeFor(kind: EmailOtpKind | null | undefined): "signup" | "email_change" {
  return kind === "email_change" ? "email_change" : "signup";
}
