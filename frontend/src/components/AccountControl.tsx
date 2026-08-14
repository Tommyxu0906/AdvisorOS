import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { AuthModal } from "./AuthModal";

/**
 * Masthead account control. Renders nothing when Supabase isn't configured — an unconfigured
 * deployment (e.g. local dev with no .env) should look like a BYOK-only app, not a broken one.
 *
 * Signed out, this is a single "Sign up / Log in" button that opens AuthModal — it does not
 * trigger Google sign-in directly, so email/password is an equally first-class path rather than
 * something buried behind a modal only Google gets to skip.
 */
export function AccountControl() {
  const { isConfigured, loading, user, signOut } = useAuth();
  const [modalOpen, setModalOpen] = useState(false);

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
    <>
      <div className="account-control">
        <button className="secondary" onClick={() => setModalOpen(true)}>
          Sign up / Log in
        </button>
      </div>
      {modalOpen && <AuthModal onClose={() => setModalOpen(false)} />}
    </>
  );
}
