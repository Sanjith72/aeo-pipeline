// v5 CH-07 — the browser Supabase client, DEGRADABLE. When NEXT_PUBLIC_SUPABASE_URL /
// NEXT_PUBLIC_SUPABASE_ANON_KEY are unset, `authEnabled` is false and everything stays
// anonymous (the marketing/free experience is byte-identical to before). The anon key is
// public by design; no secret ever ships in the bundle (the proxy's API_KEY stays
// server-only). The Bearer token is cached from onAuthStateChange so the SYNCHRONOUS
// api.ts headers() can attach it without every call site becoming async.

import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const authEnabled = Boolean(URL && ANON);

// Never construct during SSR — the session lives in the browser only.
export const supabase: SupabaseClient | null =
  authEnabled && typeof window !== "undefined"
    ? createClient(URL!, ANON!, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
      })
    : null;

// The current access token, fed by onAuthStateChange (INITIAL_SESSION / SIGNED_IN /
// TOKEN_REFRESHED / SIGNED_OUT). Read synchronously by api.ts headers().
let cachedAccessToken: string | null = null;

if (supabase) {
  supabase.auth.onAuthStateChange((_event, session) => {
    cachedAccessToken = session?.access_token ?? null;
  });
}

export function getAccessToken(): string | null {
  return cachedAccessToken;
}

/** Force-read the freshest token (refreshing if needed) — used right after a login so the
 *  claim/gated call carries the Bearer even before onAuthStateChange has fired. */
export async function currentAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  cachedAccessToken = data.session?.access_token ?? null;
  return cachedAccessToken;
}

export type { Session };
