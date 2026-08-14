/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Supabase project URL, e.g. https://xxxxx.supabase.co. Public — safe to ship in the bundle. */
  readonly VITE_SUPABASE_URL?: string;
  /** Supabase anon/public key. Public by design — RLS is what protects data, not this value. */
  readonly VITE_SUPABASE_ANON_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
