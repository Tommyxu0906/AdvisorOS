/**
 * Account state — Google OAuth or email/password, both via Supabase Auth.
 *
 * Deliberately separate from AnthropicConnectionContext. The session here identifies *who the
 * user is* — it unlocks saving run history — and has nothing to do with *what runs inference*.
 * Signing in never touches, requests, or implies an Anthropic key, and connecting a key never
 * requires signing in.
 *
 * Passwords are Supabase's problem, not this codebase's. `signUpWithPassword` /
 * `signInWithPassword` call straight into supabase-js; the password is never seen by our
 * backend and there is no column anywhere in our schema that could hold one, on purpose — see
 * supabase/migrations/0002_app_users.sql. Storing our own copy of something Supabase Auth
 * already stores correctly (hashed, salted) would only add a second place it could leak.
 *
 * One intentional asymmetry worth flagging: AnthropicConnectionContext keeps its key in memory
 * only and explicitly refuses to persist it. This context lets Supabase persist the session to
 * localStorage (the default, standard behavior for a web app session). That is not an oversight
 * — a spendable API credential and an identity session are different things with different
 * blast radii if leaked, and re-authenticating on every page refresh would make accounts
 * useless. See supabaseClient.ts.
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
  /**
   * Password auth goes through Supabase's own Email provider — the password itself never
   * touches this codebase's server or database. See supabaseClient.ts and, on the backend,
   * core/supabase_auth.py: neither one has anywhere a password could be stored even by
   * accident, because neither ever receives one.
   */
  signUpWithPassword: (
    email: string,
    password: string,
  ) => Promise<{ needsEmailConfirm: boolean; error: string | null }>;
  signInWithPassword: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
  clearError: () => void;
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

  async function signUpWithPassword(email: string, password: string) {
    if (!supabase) return { needsEmailConfirm: false, error: null };
    setError(null);
    const { data, error: authError } = await supabase.auth.signUp({ email, password });
    if (authError) {
      setError(authError.message);
      return { needsEmailConfirm: false, error: authError.message };
    }
    // Supabase's "Confirm email" setting decides this: on, signUp succeeds but returns no
    // session until the user clicks the confirmation link; off, session is populated
    // immediately and onAuthStateChange (already wired above) picks it up on its own.
    return { needsEmailConfirm: data.session === null, error: null };
  }

  async function signInWithPassword(email: string, password: string) {
    if (!supabase) return { error: null };
    setError(null);
    const { error: authError } = await supabase.auth.signInWithPassword({ email, password });
    if (authError) setError(authError.message);
    return { error: authError ? authError.message : null };
  }

  async function signOut() {
    if (!supabase) return;
    await supabase.auth.signOut();
    // Reload rather than just dropping the session. Someone's income, debts, holdings, and
    // Anthropic key all live in React state and in this tab's memory; clearing the session
    // alone would leave every one of them on screen for whoever sits down next. A reload is
    // the only way to be sure nothing survives, and it cannot go stale as state is added.
    window.location.reload();
  }

  const value = useMemo<AuthState>(
    () => ({
      user: session?.user ?? null,
      session,
      loading,
      isConfigured: isAuthConfigured,
      error,
      signInWithGoogle,
      signUpWithPassword,
      signInWithPassword,
      signOut,
      clearError: () => setError(null),
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
