"use client";

// v5 CH-07 — the client auth context. Holds the Supabase session/user and the auth-modal
// state. Fully degradable: when Supabase env is unset (`authEnabled` false) it renders its
// children untouched, `user` stays null, and no auth UI ever appears — the anonymous
// experience is identical to before.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { authEnabled, supabase, type Session } from "@/lib/supabase";
import type { AppCapabilities } from "@/lib/types";
import { AuthModal } from "./AuthModal";

export type AuthUser = { id: string; email: string | null } | null;

/** Until GET /api/config answers, assume everything is available. Optimistic on purpose:
 *  this is exactly today's behaviour (offer the path, let the 503 explain), so a slow or
 *  failed probe can never hide a Buy button that actually works. `unknown` marks it so no
 *  copy claims "not available" on the strength of a guess. */
const ASSUME_ALL: AppCapabilities = {
  payments_enabled: true, promo_enabled: true, auth_enabled: true, unknown: true,
};

interface AuthState {
  authEnabled: boolean;
  user: AuthUser;
  loading: boolean;
  authOpen: boolean;
  authReason: string | null;
  /** What this backend can actually do (v5 CH-02b). Fetched once per mount. */
  capabilities: AppCapabilities;
  openAuth: (reason?: string) => void;
  closeAuth: () => void;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser>(null);
  const [loading, setLoading] = useState(authEnabled);
  const [authOpen, setAuthOpen] = useState(false);
  const [authReason, setAuthReason] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<AppCapabilities>(ASSUME_ALL);

  // One probe per mount, regardless of auth: the answer is the same for everyone and the
  // Buy button's visibility must be decided before the user clicks, not after a 503 toast.
  // api.getConfig() never throws — it returns ASSUME_ALL-shaped optimistic defaults.
  useEffect(() => {
    let cancelled = false;
    void api.getConfig().then((c) => {
      if (!cancelled) setCapabilities(c);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!supabase) {
      setLoading(false);
      return;
    }
    const apply = (session: Session | null) => {
      setUser(session?.user ? { id: session.user.id, email: session.user.email ?? null } : null);
      setLoading(false);
      // Provision the user + claim the aeo_sid session server-side on first sight.
      if (session?.user) void api.me().catch(() => {});
    };
    supabase.auth.getSession().then(({ data }) => apply(data.session));
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => apply(session));
    return () => sub.subscription.unsubscribe();
  }, []);

  const openAuth = useCallback((reason?: string) => {
    setAuthReason(reason ?? null);
    setAuthOpen(true);
  }, []);
  const closeAuth = useCallback(() => setAuthOpen(false), []);
  const signOut = useCallback(async () => {
    await supabase?.auth.signOut();
    setUser(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ authEnabled, user, loading, authOpen, authReason, capabilities, openAuth, closeAuth, signOut }),
    [user, loading, authOpen, authReason, capabilities, openAuth, closeAuth, signOut],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {authEnabled && <AuthModal />}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) {
    // Degraded fallback so components can call useAuth() unconditionally even if the
    // provider isn't mounted (e.g. a stray SSR render) — everything reads as anonymous.
    return {
      authEnabled: false, user: null, loading: false, authOpen: false, authReason: null,
      capabilities: ASSUME_ALL,
      openAuth: () => {}, closeAuth: () => {}, signOut: async () => {},
    };
  }
  return ctx;
}
