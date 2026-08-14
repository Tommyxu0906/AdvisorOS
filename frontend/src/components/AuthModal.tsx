import { useState } from "react";
import { useAuth } from "../context/AuthContext";

/**
 * Sign up / log in, both email+password and Google, in one modal.
 *
 * The password field only ever reaches supabase-js's signUp / signInWithPassword calls — see
 * AuthContext.tsx. Nothing in this component, or anywhere downstream of it, writes a password
 * to our own backend or database.
 */
export function AuthModal({ onClose }: { onClose: () => void }) {
  const { error, clearError, signInWithGoogle, signUpWithPassword, signInWithPassword } =
    useAuth();

  const [mode, setMode] = useState<"signup" | "login">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmSent, setConfirmSent] = useState(false);

  function switchMode(next: "signup" | "login") {
    setMode(next);
    clearError();
    setConfirmSent(false);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      if (mode === "signup") {
        const { needsEmailConfirm, error: submitError } = await signUpWithPassword(
          email,
          password,
        );
        if (submitError) return;
        if (needsEmailConfirm) {
          setConfirmSent(true);
        } else {
          onClose();
        }
      } else {
        const { error: submitError } = await signInWithPassword(email, password);
        if (!submitError) onClose();
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()}>
        <div className="row-between">
          <h2>{mode === "signup" ? "Create an account" : "Log in"}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>

        <div className="mode-toggle">
          <button
            type="button"
            className={mode === "login" ? "active" : ""}
            onClick={() => switchMode("login")}
          >
            Log in
          </button>
          <button
            type="button"
            className={mode === "signup" ? "active" : ""}
            onClick={() => switchMode("signup")}
          >
            Sign up
          </button>
        </div>

        {confirmSent ? (
          <p className="muted">
            Check <strong>{email}</strong> for a confirmation link, then come back and log in.
          </p>
        ) : (
          <form onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="auth-email">Email</label>
              <input
                id="auth-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <div className="field" style={{ marginTop: 12 }}>
              <label htmlFor="auth-password">Password</label>
              <input
                id="auth-password"
                type="password"
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            {error && <p className="error">{error}</p>}

            <button type="submit" disabled={submitting} style={{ marginTop: 16, width: "100%" }}>
              {submitting
                ? mode === "signup"
                  ? "Creating account…"
                  : "Logging in…"
                : mode === "signup"
                  ? "Create account"
                  : "Log in"}
            </button>
          </form>
        )}

        <div className="divider">
          <span>or</span>
        </div>

        <button className="secondary" style={{ width: "100%" }} onClick={signInWithGoogle}>
          Continue with Google
        </button>

        <p className="fineprint" style={{ marginTop: 16 }}>
          Your password is handled entirely by Supabase Auth and never reaches this
          application's server. Signing in unlocks saved run history — it never touches, or is
          touched by, your Anthropic API key.
        </p>
      </div>
    </div>
  );
}
