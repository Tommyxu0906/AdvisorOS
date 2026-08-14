import { useAuth } from "../context/AuthContext";

/**
 * Masthead account control. Renders nothing when Supabase isn't configured — an unconfigured
 * deployment (e.g. local dev with no .env) should look like a BYOK-only app, not a broken one.
 */
export function AccountControl() {
  const { isConfigured, loading, user, error, signInWithGoogle, signOut } = useAuth();

  if (!isConfigured) return null;
  if (loading) return <span className="account-control muted small">…</span>;

  if (user) {
    const label = user.user_metadata?.full_name || user.email || "Signed in";
    return (
      <div className="account-control">
        <span className="account-name small">{label}</span>
        <button className="ghost" onClick={signOut}>
          Sign out
        </button>
      </div>
    );
  }

  return (
    <div className="account-control">
      <button className="secondary" onClick={signInWithGoogle}>
        Sign in with Google
      </button>
      {error && <span className="error-inline small">{error}</span>}
    </div>
  );
}
