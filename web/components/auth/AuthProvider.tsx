"use client";

// v5 CH-07 — the client auth context. Holds the Supabase session/user and the auth-modal
// state. Fully degradable: when Supabase env is unset (`authEnabled` false) it renders its
// children untouched, `user` stays null, and no auth UI ever appears — the anonymous
// experience is identical to before.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { authEnabled, supabase, type Session } from "@/lib/supabase";
import { AuthModal } from "./AuthModal";

export type AuthUser = { id: string; email: string | null } | null;

interface AuthState {
  authEnabled: boolean;
  user: AuthUser;
  loading: boolean;
  authOpen: boolean;
  authReason: string | null;
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
    () => ({ authEnabled, user, loading, authOpen, authReason, openAuth, closeAuth, signOut }),
    [user, loading, authOpen, authReason, openAuth, closeAuth, signOut],
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
      openAuth: () => {}, closeAuth: () => {}, signOut: async () => {},
    };
  }
  return ctx;
}
