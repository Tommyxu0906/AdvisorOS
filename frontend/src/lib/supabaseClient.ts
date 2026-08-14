/**
 * Supabase client for account auth only.
 *
 * This is a distinct concern from AnthropicConnectionContext: the Supabase session identifies
 * *who the user is* (for saving profiles and run history later); the Anthropic key is *what
 * runs inference* and is never touched by anything in this file. Do not merge the two.
 *
 * Configured via VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY, the same pattern as
 * VITE_API_BASE_URL in api.ts. The anon key is meant to ship in a public bundle — Supabase's
 * row-level security is what actually protects data, not keeping this value secret.
 *
 * When unset (e.g. local dev without a configured project), `supabase` is null and
 * AuthContext degrades to a signed-out, auth-unavailable state rather than throwing.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase: SupabaseClient | null =
  url && anonKey
    ? createClient(url, anonKey, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
        },
      })
    : null;

export const isAuthConfigured = supabase !== null;
