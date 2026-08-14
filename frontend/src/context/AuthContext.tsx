/**
 * Google account state, via Supabase Auth.
 *
 * Deliberately separate from AnthropicConnectionContext. The session here identifies *who the
 * user is* — it will unlock saving profiles and run history later — and has nothing to do with
 * *what runs inference*. Signing in with Google never touches, requests, or implies an
 * Anthropic key, and connecting a key never requires signing in.
 *
 * One intentional asymmetry worth flagging: AnthropicConnectionContext keeps its key in memory
 * only and explicitly refuses to persist it. This context lets Supabase persist the session to
 * localStorage (the default, standard behavior for a web app session). That is not an oversight
 * — a spendable API credential and an identity session are different things with different
 * blast radii if leaked, and re-authenticating with Google on every page refresh would make
 * accounts useless. See supabaseClient.ts.
 */

import type { Session, User } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { isAuthConfigured, supabase } from "../lib/supabaseClient";

interface AuthState {
  user: User | null;
  session: Session | null;
  /** True once the initial session check has resolved. */
  loading: boolean;
  /** False when VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are unset — sign-in is hidden, not broken. */
  isConfigured: boolean;
  error: string | null;
  signInWithGoogle: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(isAuthConfigured);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) return;

    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });

    return () => subscription.unsubscribe();
  }, []);

  async function signInWithGoogle() {
    if (!supabase) return;
    setError(null);
    const { error: authError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
    if (authError) setError(authError.message);
    // On success the browser navigates away to Google, then back — nothing further to do here.
  }

  async function signOut() {
    if (!supabase) return;
    await supabase.auth.signOut();
  }

  const value = useMemo<AuthState>(
    () => ({
      user: session?.user ?? null,
      session,
      loading,
      isConfigured: isAuthConfigured,
      error,
      signInWithGoogle,
      signOut,
    }),
    [session, loading, error],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return ctx;
}
